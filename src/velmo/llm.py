"""Clients LLM : Azure AI Inference (Mistral-Large-3, agent principal), Claude
via Azure AI Foundry (`AnthropicFoundry`, extracteur mémoire) et repli local
hors-ligne.

L'import des SDK est différé pour que le harness démarre et que les tests
tournent sans dépendre du SDK ni d'un endpoint joignable.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Protocol, cast, runtime_checkable

from velmo.config import get_settings, require

logger = logging.getLogger(__name__)


@runtime_checkable
class LLM(Protocol):
    """Interface minimale d'un client de complétion."""

    def invoke(self, system: str, context: str, message: str) -> str: ...


class EchoLLM:
    """Repli déterministe et hors-ligne : renvoie un accusé de réception.

    Permet au harness de conversation de démarrer sans identifiants Azure — en
    dev/CI uniquement (cf. `get_llm`) ; jamais censé être atteint en production
    (contrat de démarrage, `docs/reference/conceptions/conception_chantier1_memoire.md`
    §Contrat de démarrage). Loggue un warning à chaque instanciation : un repli
    non voulu doit être visible immédiatement, pas découvert après coup.
    """

    def __init__(self) -> None:
        logger.warning(
            "EchoLLM instancié : aucune réponse réelle ne sera générée. "
            "Attendu uniquement en dev/CI (ENVIRONMENT != 'production')."
        )

    def invoke(self, system: str, context: str, message: str) -> str:
        return f"[velmo] J'ai bien reçu : {message}"


class _MinIntervalRateLimiter:
    """Espace chaque appel d'un délai minimal fixe, dès le tout premier appel.

    Le déploiement Azure `Mistral-Large-3` (`sconanRG`/`sconanext-8976-resource`,
    francecentral) est plafonné à 20 requêtes/min — et la subscription (tenant
    de formation partagé) est déjà au plafond régional pour ce modèle
    (`az cognitiveservices usage list` : `AIServices.GlobalStandard.Mistral-Large-3`
    = 20/20), donc pas de marge côté Azure pour absorber les rafales (suite
    Qualité + résumés mémoire pendant un run du gate, ou un run concurrent d'un
    chat manuel).

    Une première version autorisait une rafale de N appels sans délai avant de
    commencer à freiner (fenêtre glissante classique) — insuffisant en
    pratique : un seul tour d'agent peut déclencher plusieurs appels Mistral
    (routage + génération), et un cas en échec en déclenche un second
    (`with_retry`) ; la rafale initiale suffisait déjà à dépasser le vrai
    plafond de 20/min avant même que le freinage ne s'engage (429 observés dès
    les premières secondes du process). Un espacement fixe dès le 1er appel
    élimine toute rafale, plus sûr sous un plafond aussi bas. Verrou tenu
    pendant le `sleep()` : sérialise volontairement les appelants concurrents
    (threads du threadpool FastAPI) plutôt que de les laisser se réveiller
    ensemble et redépasser la limite."""

    def __init__(self, min_interval_s: float) -> None:
        self._min_interval_s = min_interval_s
        self._last_call: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._last_call is not None:
                elapsed = now - self._last_call
                if elapsed < self._min_interval_s:
                    time.sleep(self._min_interval_s - elapsed)
            self._last_call = time.monotonic()


# 4.5s entre appels (~13/min, sous les 20/min réels) : cf. docstring
# `_MinIntervalRateLimiter`. La marge (13 vs 20) absorbe les appels multiples
# par tour d'agent et les retries. Partagé par toutes les instances
# d'`AzureLLM` du process (module-level) — c'est le quota Azure du
# déploiement qui est global, pas un budget par instance.
_MISTRAL_RATE_LIMITER = _MinIntervalRateLimiter(min_interval_s=4.5)


def _estimate_tokens(*texts: str) -> int:
    """Heuristique 4 caractères ≈ 1 token — même convention que
    `MemoryManager` (`memory/__init__.py`) et `mlops/observability.py`."""
    return sum(max(1, len(t) // 4) for t in texts)


class _TokenBucket:
    """Seau à jetons : se remplit à `rate_per_s` tokens/seconde jusqu'à
    `capacity`, `wait(n)` bloque jusqu'à ce que `n` jetons soient
    disponibles.

    Le plafond req/min (`_MinIntervalRateLimiter`) ne protège pas d'un
    plafond tokens/min séparé : le déploiement `Mistral-Large-3` est aussi
    limité à 20 000 tokens/min, et les suites qualité/mémoire du gate
    envoient des prompts (contexte + historique) et attendent des réponses
    complètes — quelques appels volumineux suffisent à épuiser ce budget
    bien avant que le rythme de 13/min ne pose problème. Constaté en prod :
    Azure met la requête en file jusqu'à épuisement du timeout client
    (`openai.APITimeoutError` à 45s), pour des appels pourtant sous le
    plafond de requêtes."""

    def __init__(self, capacity: float, rate_per_s: float) -> None:
        self._capacity = capacity
        self._rate_per_s = rate_per_s
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def wait(self, needed: float) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_s)
                self._last_refill = now
                if self._tokens >= needed:
                    self._tokens -= needed
                    return
                time.sleep((needed - self._tokens) / self._rate_per_s)


# Capacité 18 000 (marge sous les 20 000/min réels — l'estimation par
# caractères peut sous-évaluer le compte réel de tokens) rechargée à
# 300/s (18 000/60).
_MISTRAL_TOKEN_BUDGET = _TokenBucket(capacity=18_000, rate_per_s=300.0)

# Budget de complétion pas connu à l'avance (pas de champ `usage` exposé
# avant l'appel) — estimation conservatrice pour une réponse d'agent de
# support typique, cf. `_TokenBucket`.
_EXPECTED_COMPLETION_TOKENS = 800


class AzureLLM:
    """Adapte le modèle de chat Azure AI Inference à l'interface `LLM`."""

    def __init__(self, model: Any) -> None:
        self._model = model

    def invoke(self, system: str, context: str, message: str) -> str:
        messages = [{"role": "system", "content": system}]
        if context:
            messages.append({"role": "system", "content": f"Mémoire:\n{context}"})
        messages.append({"role": "user", "content": message})
        _MISTRAL_RATE_LIMITER.wait()
        estimated_tokens = _estimate_tokens(system, context, message) + _EXPECTED_COMPLETION_TOKENS
        _MISTRAL_TOKEN_BUDGET.wait(estimated_tokens)
        return cast(str, self._model.invoke(messages).content)


class AnthropicLLM:
    """Client Claude via Azure AI Foundry (`AnthropicFoundry`), utilisé par
    l'extracteur mémoire — déploiement asynchrone (`anthropic_*`), distinct du
    déploiement Azure OpenAI dédié au juge garde-fous (voir Settings, Q1
    session de grilling). `base_url` pointe la ressource Foundry, pas l'API
    Anthropic directe (`api.anthropic.com`) — exemple fourni par Azure :
    `AnthropicFoundry(api_key=..., base_url="https://<resource>.services.ai.azure.com/anthropic")`.
    """

    def __init__(self, endpoint: str, api_key: str, model: str) -> None:
        from anthropic import AnthropicFoundry  # import différé : dépendance optionnelle

        self._client = AnthropicFoundry(api_key=api_key, base_url=endpoint)
        self._model = model

    def invoke(self, system: str, context: str, message: str) -> str:
        full_system = f"{system}\n\nMémoire:\n{context}" if context else system
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=full_system,
            messages=[{"role": "user", "content": message}],
        )
        block = response.content[0]
        return block.text if block.type == "text" else ""


def get_llm() -> LLM:
    """Construit le client Azure si configuré, sinon le repli `EchoLLM`.

    En production (`Settings.environment == "production"`), l'absence de
    configuration LLM est une erreur de démarrage (fail-fast) — pas un repli
    silencieux qui servirait du contenu mock à de vrais clients (cf. contrat de
    démarrage, Chantier 1). En dev/CI, `EchoLLM` reste un repli toléré.
    """
    settings = get_settings()
    if not settings.azure_ai_inference_endpoint:
        if settings.environment == "production":
            raise RuntimeError(
                "Configuration LLM absente en production : "
                "`AZURE_AI_INFERENCE_ENDPOINT` doit être défini "
                "(ENVIRONMENT=production interdit le repli EchoLLM)."
            )
        return EchoLLM()

    from langchain_azure_ai.chat_models import AzureAIOpenAIApiChatModel

    model = AzureAIOpenAIApiChatModel(
        endpoint=settings.azure_ai_inference_endpoint,
        credential=require(settings.azure_ai_inference_api_key, "AZURE_AI_INFERENCE_API_KEY"),
        model=settings.azure_ai_inference_model,
        # Sans timeout explicite, un appel réseau resté sans réponse (endpoint
        # lent/indisponible) bloque indéfiniment — observé en pratique lors
        # des tests de bout en bout de l'interface pédagogique (chat/stream).
        request_timeout=45.0,
        # 0, pas 1 : un retry interne au client re-tape l'endpoint SANS
        # repasser par `_MISTRAL_RATE_LIMITER`/`_MISTRAL_TOKEN_BUDGET` —
        # aggrave la pression pile quand le quota tokens/min est déjà
        # saturé (cause du run de release qui a expiré à 45s malgré le
        # pacing). Le retry de niveau suite (`with_retry`,
        # `mlops/suites/*.py`) rejoue l'appel via `AzureLLM.invoke`, donc
        # respecte le rate limiter — c'est le seul retry voulu ici.
        max_retries=0,
        # Cette ressource expose l'endpoint OpenAI-compatible `/openai/v1` ;
        # la Responses API (défaut de la classe) n'y répond jamais et fait
        # tourner l'appel jusqu'au timeout — confirmé en isolant l'appel
        # (~90s avant `APITimeoutError`). Chat Completions y répond en
        # quelques secondes.
        use_responses_api=False,
    )
    return AzureLLM(model)
