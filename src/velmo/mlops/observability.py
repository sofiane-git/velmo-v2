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

import time
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
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

    def __init__(self) -> None:
        from langfuse import Langfuse

        from velmo.config import get_settings, require

        settings = get_settings()
        self._client = Langfuse(
            public_key=require(settings.langfuse_public_key, "LANGFUSE_PUBLIC_KEY"),
            secret_key=require(settings.langfuse_secret_key, "LANGFUSE_SECRET_KEY"),
            base_url=require(settings.langfuse_base_url, "LANGFUSE_BASE_URL"),
            mask=mask_sensitive_data,
        )
        self._trace_id: str = self._client.create_trace_id()

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
        generation = self._client.start_observation(
            trace_context={"trace_id": self._trace_id},
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

    def __init__(self, inner: "LLM", sink: ObservabilitySink, component: str, model: str) -> None:
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
        self._sink.on_llm_call(
            self._component, tokens, latency_ms, cost,
            input=message, output=result, model=self._model,
        )
        return result


class InstrumentedExtractor:
    """Enveloppe un `FactExtractor` (`MemoryManager.extractor`, injectable) —
    composant "extracteur mémoire" de l'instrumentation locale."""

    def __init__(
        self, inner: "FactExtractor", sink: ObservabilitySink, component: str, model: str
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
        self._sink.on_llm_call(
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
        self, inner: "ModerationClassifier", sink: ObservabilitySink, component: str
    ) -> None:
        self._inner = inner
        self._sink = sink
        self._component = component

    def _record(self, text: str, start: float, output: dict[str, float] | "ClassifierResult" | None = None) -> None:
        latency_ms = (time.monotonic() - start) * 1000
        tokens = _estimate_tokens(text)
        cost = estimate_cost(tokens, "local-classifier")
        self._sink.on_llm_call(
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

    def __init__(self, inner: "Judge", sink: ObservabilitySink, component: str, model: str) -> None:
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
        self._sink.on_llm_call(
            self._component, tokens, latency_ms, cost,
            input=text, output=str(result), model=self._model,
        )
        return result
