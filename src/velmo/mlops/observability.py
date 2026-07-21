"""Observabilité : interface pluggable (`ObservabilitySink`), implémentation
par défaut no-op (`NullSink`), implémentation réelle (`LangfuseSink`, Langfuse
Cloud EU — voir deploy/langfuse/README.md pour la config). `eval_run` ne stocke qu'un pointeur
(`langfuse_trace_url`), jamais la donnée de décision — voir
conception_chantier3_evaluation_mlops.md §Observabilité.

Les wrappers `Instrumented*` ci-dessous réalisent le "décorateur sur chaque
appel LLM (agent, extracteur mémoire Ch.1, classifieur + LLM-juge Ch.2)" —
voir conception §Instrumentation locale — par **composition pure** autour des
Protocols déjà exposés (`velmo.llm.LLM`, `velmo.memory.extractor.FactExtractor`,
`velmo.guardrails.classifier.ModerationClassifier`, `velmo.guardrails.judge.Judge`) :
zéro modification des classes `MemoryManager`/`GuardrailEngine`/`Agent`, qui
acceptent déjà ces composants en injection de constructeur.
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import Iterator
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from langfuse import Langfuse

    from velmo.agent import Agent
    from velmo.guardrails.classifier import ClassifierResult, ModerationClassifier
    from velmo.guardrails.judge import Judge
    from velmo.llm import LLM
    from velmo.memory.extractor import ExtractionResult, FactExtractor


def mask_sensitive_data(*, data: Any, **_: Any) -> Any:
    """Hook Langfuse legacy `mask=` (couvre `start_observation()`/`update()`,
    tout ce que ce module envoie) — réutilise les mêmes règles que le
    guardrail G4/G7 (`redact_pii`/`redact_secret_leak`) plutôt que dupliquer
    des regex : la définition de ce qui est sensible reste unique."""
    from velmo.guardrails import redact_pii, redact_secret_leak

    if isinstance(data, str):
        return redact_secret_leak(redact_pii(data))
    if isinstance(data, dict):
        return {key: mask_sensitive_data(data=value) for key, value in data.items()}
    if isinstance(data, list):
        return [mask_sensitive_data(data=item) for item in data]
    return data


class ObservabilitySink(Protocol):
    def on_llm_call(
        self,
        component: str,
        tokens: int,
        latency_ms: float,
        cost: float,
        *,
        input: str | None = None,
        output: str | None = None,
        model: str | None = None,
    ) -> None: ...
    def run_url(self, run_id: str) -> str | None: ...


class NullSink:
    """Implémentation par défaut : n'émet rien, ne stocke rien. Le gate ne
    dépend jamais de ce sink pour fonctionner (principe directeur du
    Chantier 3) — un vrai sink Langfuse serait branché ici sans changer
    l'appelant."""

    def on_llm_call(
        self,
        component: str,
        tokens: int,
        latency_ms: float,
        cost: float,
        *,
        input: str | None = None,
        output: str | None = None,
        model: str | None = None,
    ) -> None:
        return None

    def run_url(self, run_id: str) -> str | None:
        return None


_current_sink: contextvars.ContextVar[ObservabilitySink] = contextvars.ContextVar(
    "velmo_current_observability_sink", default=NullSink()
)


def set_current_sink(sink: ObservabilitySink) -> "contextvars.Token[ObservabilitySink]":
    """Pose le sink pour tout `Instrumented*` construit avec `sink=None` dans
    le contexte courant — utilisé par `traced_respond` (Task 5) pour donner
    à l'agent un sink différent à chaque tour de conversation sans
    reconstruire `MemoryManager`/`GuardrailEngine` (coûteux, singletons)."""
    return _current_sink.set(sink)


def reset_current_sink(token: "contextvars.Token[ObservabilitySink]") -> None:
    _current_sink.reset(token)


def _resolve_sink(explicit: ObservabilitySink | None) -> ObservabilitySink:
    return explicit if explicit is not None else _current_sink.get()


class CostAccumulatingSink:
    """Enveloppe un `ObservabilitySink` quelconque (Langfuse réel ou
    `NullSink`) pour accumuler `total_cost` localement — l'agrégat qui gate
    (`Scores.cost`, `EvalRun.cost_per_conv`) ne doit jamais dépendre de la
    présence d'un sink externe (principe directeur du chantier : Langfuse
    hors chemin de gate). `run_eval` (Task 6) enveloppe systématiquement le
    `sink` reçu avec celle-ci avant de le passer aux suites."""

    def __init__(self, inner: ObservabilitySink) -> None:
        self._inner = inner
        self.total_cost = 0.0

    def on_llm_call(
        self,
        component: str,
        tokens: int,
        latency_ms: float,
        cost: float,
        *,
        input: str | None = None,
        output: str | None = None,
        model: str | None = None,
    ) -> None:
        self.total_cost += cost
        self._inner.on_llm_call(
            component, tokens, latency_ms, cost, input=input, output=output, model=model
        )

    def run_url(self, run_id: str) -> str | None:
        return self._inner.run_url(run_id)


class LangfuseSink:
    """Sink réel — Langfuse Cloud, région EU (conception §Observabilité —
    projet pédagogique, pas de vraies conversations client en prod ;
    self-host resterait la bonne pratique sinon, voir §Gouvernance RGPD).
    Un trace_id est généré
    à l'instanciation (`create_trace_id()`, aléatoire) et réutilisé pour
    **chaque** `on_llm_call` via `trace_context={"trace_id": ...}` — pas via
    le contexte OTel "actif" du thread courant : l'extraction mémoire tourne
    parfois sur `_BACKGROUND_EXECUTOR` (Chantier 1), un thread différent de
    celui qui a créé le sink, et la propagation de contexte OTel par défaut
    ne traverse pas les threads de façon fiable. Chaque appel s'ancre donc
    explicitement au même trace_id, indépendamment du thread qui l'émet.

    Cette classe ne gate jamais rien (principe directeur du chantier) : si
    Langfuse est down, `on_llm_call` peut lever — c'est `run_eval` (Task 6)
    qui décide comment isoler ça, pas cette classe elle-même."""

    def __init__(
        self,
        *,
        client: "Langfuse | None" = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> None:
        if client is None:
            from langfuse import Langfuse

            from velmo.config import get_settings, require

            settings = get_settings()
            client = Langfuse(
                public_key=require(settings.langfuse_public_key, "LANGFUSE_PUBLIC_KEY"),
                secret_key=require(settings.langfuse_secret_key, "LANGFUSE_SECRET_KEY"),
                base_url=require(settings.langfuse_base_url, "LANGFUSE_BASE_URL"),
                mask=mask_sensitive_data,
            )
        self._client = client
        self._trace_id: str = trace_id or self._client.create_trace_id()
        self._parent_span_id = parent_span_id

    def on_llm_call(
        self,
        component: str,
        tokens: int,
        latency_ms: float,
        cost: float,
        *,
        input: str | None = None,
        output: str | None = None,
        model: str | None = None,
    ) -> None:
        trace_context_dict: dict[str, str] = {"trace_id": self._trace_id}
        if self._parent_span_id is not None:
            trace_context_dict["parent_span_id"] = self._parent_span_id
        generation = self._client.start_observation(  # type: ignore[call-overload]
            trace_context=trace_context_dict,
            name=component,
            as_type="generation",
            input=input,
            output=output,
            model=model,
            usage_details={"total": tokens},
            cost_details={"total": cost},
            metadata={"latency_ms": latency_ms},
        )
        generation.end()

    def run_url(self, run_id: str) -> str | None:
        return self._client.get_trace_url(trace_id=self._trace_id)

    def close(self) -> None:
        """À appeler après `run_eval` dans un process court-vécu (CLI) — le
        SDK bufferise en arrière-plan, `flush()` force l'envoi avant sortie
        du process (doc SDK §Client lifecycle & flushing). Pas une méthode du
        Protocol `ObservabilitySink` (optionnelle, `NullSink` n'en a pas
        besoin) : l'appelant fait `getattr(sink, "close", lambda: None)()`."""
        self._client.flush()


def get_sink() -> ObservabilitySink:
    """`LangfuseSink` si `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/
    `LANGFUSE_BASE_URL` sont définis, sinon `NullSink` — même contrat de
    repli gracieux que `get_llm`/`get_classifier`/`get_judge`/`get_extractor`/
    `get_quality_scorer`. Un échec de connexion au moment de construire le
    client (`LangfuseSink()`) retombe aussi sur `NullSink` plutôt que de
    faire échouer tout `run_eval` pour une raison purement observabilité —
    même logique que `get_quality_scorer`."""
    from velmo.config import get_settings

    settings = get_settings()
    if settings.langfuse_public_key and settings.langfuse_secret_key and settings.langfuse_base_url:
        try:
            return LangfuseSink()
        except Exception:
            return NullSink()
    return NullSink()


@lru_cache(maxsize=1)
def get_langfuse_client() -> "Langfuse | None":
    """Client Langfuse **partagé**, pour un process long-vécu (API live) —
    contrairement à `get_sink()` (construit un `LangfuseSink` complet, un par
    appel, pour un process court-vécu comme le gate CI), ceci mémoïse le
    `Langfuse(...)` lui-même : un tour de conversation ne doit jamais rouvrir
    une connexion. Repli sur `None` (jamais d'exception) — même contrat que
    `get_sink()`."""
    from velmo.config import get_settings

    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key and settings.langfuse_base_url):
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
            mask=mask_sensitive_data,
        )
    except Exception:
        return None


_STAGE_EVENTS = ("input_guardrail", "memory_read", "routing", "tool_result", "output_guardrail", "memory_write")


def _stage_as_type(event_type: str) -> Literal[
    "generation", "embedding", "span", "agent", "tool", "chain", "retriever", "evaluator", "guardrail"
]:
    if event_type in ("input_guardrail", "output_guardrail"):
        return "guardrail"
    if event_type == "memory_read":
        return "retriever"
    if event_type == "tool_result":
        return "tool"
    return "span"


def traced_respond(
    agent: "Agent", user_id: str, message: str
) -> "Iterator[tuple[str, dict[str, Any]]]":
    """Enveloppe `Agent.respond_traced` (déjà émetteur d'événements par étape
    — `src/velmo/agent.py`) pour produire un vrai arbre de spans Langfuse par
    tour de conversation : un tour = un trace_id frais, chaque étape émise par
    `respond_traced` devient un span enfant de la racine `chat-turn`, chaque
    appel LLM (via `Instrumented*`, résolu dynamiquement — Task 3) devient lui
    aussi un enfant direct de la racine via le `LangfuseSink` posé dans le
    contexte pour la durée du tour. Passthrough pur si Langfuse n'est pas
    configuré (`get_langfuse_client()` renvoie `None`).

    Racine créée via `start_as_current_observation` (pas `start_observation`)
    délibérément : les enfants sont créés hors du contexte OTel ambiant
    (`trace_context={"trace_id":, "parent_span_id":}` explicite, seule option
    fiable pour l'extraction mémoire — tourne sur `_BACKGROUND_EXECUTOR`, un
    thread différent), donc aucune observation n'est jamais "current" au sens
    OTel — sans `start_as_current_observation`, Langfuse ne sait pas laquelle
    des observations d'un même trace_id en est la racine "officielle"
    (`is_app_root`), et dérive le nom/input/output affichés au niveau trace de
    l'observation arrivée en premier à l'ingestion — un ordre réseau/async,
    pas l'ordre de création. Constaté en pratique (audit Task 6) : la
    génération `memory_extractor`, émise sur le thread d'arrière-plan,
    devançait parfois `chat-turn` côté ingestion et le trace entier
    s'affichait sous son nom. `start_as_current_observation` marque
    explicitement `chat-turn` comme racine, indépendamment de tout ordre
    d'arrivée."""
    client = get_langfuse_client()
    if client is None:
        yield from agent.respond_traced(user_id, message)
        return

    trace_id = client.create_trace_id()
    with client.start_as_current_observation(
        trace_context={"trace_id": trace_id},
        name="chat-turn",
        as_type="span",
        input={"message": message},
        metadata={"user_id": user_id},
    ) as root:
        turn_sink = LangfuseSink(client=client, trace_id=trace_id, parent_span_id=root.id)
        token = set_current_sink(turn_sink)
        answer = ""
        status = "error"
        try:
            for event_type, payload in agent.respond_traced(user_id, message):
                if event_type in _STAGE_EVENTS:
                    span_name = event_type.replace("_", "-")
                    if event_type == "tool_result":
                        tool_name = payload.get("name")
                        if isinstance(tool_name, str):
                            span_name = tool_name
                    child = client.start_observation(  # type: ignore[call-overload]
                        trace_context={"trace_id": trace_id, "parent_span_id": root.id},
                        name=span_name,
                        as_type=_stage_as_type(event_type),
                        output=payload,
                    )
                    child.end()
                elif event_type == "final":
                    answer = str(payload.get("answer", ""))
                    status = str(payload.get("status", "ok"))
                yield event_type, payload
        finally:
            reset_current_sink(token)
            root.update(output={"answer": answer}, metadata={"status": status})
            # `root.update()` seul ne suffit pas : le nom/input/output affichés
            # au niveau *trace* (pas de l'observation) sont dérivés par
            # Langfuse de l'observation traitée en dernier à l'ingestion — un
            # ordre réseau/async, pas l'ordre de création (constaté en
            # pratique, audit Task 6 : `memory_extractor`, sur le thread
            # d'arrière-plan, l'emportait souvent sur `chat-turn`).
            # `set_trace_io` fixe explicitement le résumé du trace,
            # indépendamment de cette course.
            root.set_trace_io(input={"message": message}, output={"answer": answer})


def traced_reply(agent: "Agent", user_id: str, message: str) -> str:
    """Équivalent bloquant de `traced_respond`, même contrat que
    `Agent.respond` (draine le générateur, ne garde que la réponse finale)."""
    answer = ""
    for event_type, payload in traced_respond(agent, user_id, message):
        if event_type == "final":
            answer = str(payload["answer"])
    return answer


def estimate_cost(tokens: int, model: str) -> float:
    """Coût estimé (€) pour `tokens` tokens consommés par `model`, selon la
    table de tarifs versionnée (`Settings.token_pricing`). `0.0` (pas une
    exception) pour un modèle non tarifé — un tarif manquant ne doit jamais
    faire échouer un calcul de coût agrégé."""
    from velmo.config import get_settings

    price_per_1000 = get_settings().token_pricing.get(model, 0.0)
    return (tokens / 1000) * price_per_1000


def _estimate_tokens(*texts: str) -> int:
    """Heuristique 4 caractères ≈ 1 token — même convention que
    `MemoryManager` (`memory/__init__.py`, calcul de `thread.token_count`) :
    aucun client LLM du codebase n'expose de champ `usage` exact aujourd'hui,
    mais cet ordre de grandeur reste cohérent avec le reste de l'agent."""
    return sum(max(1, len(t) // 4) for t in texts)


class InstrumentedLLM:
    """Enveloppe un `velmo.llm.LLM` (utilisé par `Agent.llm` et
    `MemoryManager.llm`, tous deux injectables par constructeur) pour émettre
    un `on_llm_call` par appel."""

    def __init__(
        self, inner: "LLM", sink: ObservabilitySink | None, component: str, model: str
    ) -> None:
        self._inner = inner
        self._sink = sink
        self._component = component
        self._model = model

    def invoke(self, system: str, context: str, message: str) -> str:
        start = time.monotonic()
        result = self._inner.invoke(system, context, message)
        latency_ms = (time.monotonic() - start) * 1000
        tokens = _estimate_tokens(system, context, message, result)
        cost = estimate_cost(tokens, self._model)
        _resolve_sink(self._sink).on_llm_call(
            self._component, tokens, latency_ms, cost,
            input=message, output=result, model=self._model,
        )
        return result


class InstrumentedExtractor:
    """Enveloppe un `FactExtractor` (`MemoryManager.extractor`, injectable) —
    composant "extracteur mémoire" de l'instrumentation locale."""

    def __init__(
        self, inner: "FactExtractor", sink: ObservabilitySink | None, component: str, model: str
    ) -> None:
        self._inner = inner
        self._sink = sink
        self._component = component
        self._model = model

    def extract(self, user_message: str, assistant_message: str) -> "ExtractionResult":
        start = time.monotonic()
        result = self._inner.extract(user_message, assistant_message)
        latency_ms = (time.monotonic() - start) * 1000
        tokens = _estimate_tokens(user_message, assistant_message)
        cost = estimate_cost(tokens, self._model)
        _resolve_sink(self._sink).on_llm_call(
            self._component, tokens, latency_ms, cost,
            input=user_message, output=str(result), model=self._model,
        )
        return result


class InstrumentedClassifier:
    """Enveloppe un `ModerationClassifier` (`GuardrailEngine.classifier`,
    injectable). Coût toujours nul (`estimate_cost` renvoie 0.0 pour un
    modèle absent de `token_pricing`) : le classifieur local (LlamaGuard via
    Ollama/lexique) n'est pas facturé au token Azure."""

    def __init__(
        self, inner: "ModerationClassifier", sink: ObservabilitySink | None, component: str
    ) -> None:
        self._inner = inner
        self._sink = sink
        self._component = component

    def _record(self, text: str, start: float, output: dict[str, float] | "ClassifierResult" | None = None) -> None:
        latency_ms = (time.monotonic() - start) * 1000
        tokens = _estimate_tokens(text)
        cost = estimate_cost(tokens, "local-classifier")
        _resolve_sink(self._sink).on_llm_call(
            self._component, tokens, latency_ms, cost,
            input=text, output=str(output), model="local-classifier",
        )

    def score(self, text: str) -> dict[str, float]:
        start = time.monotonic()
        result = self._inner.score(text)
        self._record(text, start, result)
        return result

    def score_detailed(self, text: str) -> "ClassifierResult":
        start = time.monotonic()
        result = self._inner.score_detailed(text)
        self._record(text, start, result)
        return result


class InstrumentedJudge:
    """Enveloppe un `Judge` (`GuardrailEngine.judge`, injectable) — composant
    "LLM-juge Ch.2" de l'instrumentation locale."""

    def __init__(
        self, inner: "Judge", sink: ObservabilitySink | None, component: str, model: str
    ) -> None:
        self._inner = inner
        self._sink = sink
        self._component = component
        self._model = model

    def evaluate(self, text: str, agent_response: str | None = None) -> dict[str, float | str]:
        start = time.monotonic()
        result = self._inner.evaluate(text, agent_response)
        latency_ms = (time.monotonic() - start) * 1000
        tokens = _estimate_tokens(text, agent_response or "")
        cost = estimate_cost(tokens, self._model)
        _resolve_sink(self._sink).on_llm_call(
            self._component, tokens, latency_ms, cost,
            input=text, output=str(result), model=self._model,
        )
        return result
