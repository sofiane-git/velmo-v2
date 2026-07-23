"""Clients LLM : Azure AI Inference (Mistral-Large-3) et repli local hors-ligne.

L'import du SDK Azure est différé pour que le harness démarre et que les tests
tournent sans dépendre du SDK ni d'un endpoint joignable.
"""

from __future__ import annotations

import logging
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
    (contrat de démarrage, `docs/job/conceptions/conception_chantier1_memoire.md`
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


class AzureLLM:
    """Adapte le modèle de chat Azure AI Inference à l'interface `LLM`."""

    def __init__(self, model: Any) -> None:
        self._model = model

    def invoke(self, system: str, context: str, message: str) -> str:
        messages = [{"role": "system", "content": system}]
        if context:
            messages.append({"role": "system", "content": f"Mémoire:\n{context}"})
        messages.append({"role": "user", "content": message})
        return cast(str, self._model.invoke(messages).content)


class AzureOpenAILLM:
    """Client Azure OpenAI (chat completions), utilisé par l'extracteur
    mémoire — déploiement asynchrone (`azure_openai_async_*`), distinct du
    déploiement dédié au juge garde-fous (voir Settings, Q1 session de grilling).
    """

    def __init__(self, endpoint: str, api_key: str, deployment: str) -> None:
        from openai import OpenAI  # import différé : dépendance optionnelle

        self._client = OpenAI(base_url=endpoint, api_key=api_key, timeout=45.0)
        self._deployment = deployment

    def invoke(self, system: str, context: str, message: str) -> str:
        messages = [{"role": "system", "content": system}]
        if context:
            messages.append({"role": "system", "content": f"Mémoire:\n{context}"})
        messages.append({"role": "user", "content": message})
        completion = self._client.chat.completions.create(
            model=self._deployment, messages=messages  # type: ignore[arg-type]
        )
        return completion.choices[0].message.content or ""


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
        max_retries=1,
        # Cette ressource expose l'endpoint OpenAI-compatible `/openai/v1` ;
        # la Responses API (défaut de la classe) n'y répond jamais et fait
        # tourner l'appel jusqu'au timeout — confirmé en isolant l'appel
        # (~90s avant `APITimeoutError`). Chat Completions y répond en
        # quelques secondes.
        use_responses_api=False,
    )
    return AzureLLM(model)
