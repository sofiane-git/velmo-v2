"""Clients LLM : Azure AI Inference (Mistral-Large-3) et repli local hors-ligne.

L'import du SDK Azure est différé pour que le harness démarre et que les tests
tournent sans dépendre du SDK ni d'un endpoint joignable.
"""

from __future__ import annotations

from typing import Any, Protocol, cast

from velmo.config import get_settings, require


class LLM(Protocol):
    """Interface minimale d'un client de complétion."""

    def invoke(self, system: str, context: str, message: str) -> str: ...


class EchoLLM:
    """Repli déterministe et hors-ligne : renvoie un accusé de réception.

    Permet au harness de conversation de démarrer sans identifiants Azure.
    """

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


def get_llm() -> LLM:
    """Construit le client Azure si configuré, sinon le repli `EchoLLM`."""
    settings = get_settings()
    if not settings.azure_ai_inference_endpoint:
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
