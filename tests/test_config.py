from __future__ import annotations

import pytest

from velmo.config import ConfigurationError, Settings, require, validate_startup


def test_require_returns_value_when_present():
    assert require("http://example.com", "SOME_VAR") == "http://example.com"


def test_require_raises_key_error_when_missing():
    with pytest.raises(KeyError, match="SOME_VAR"):
        require(None, "SOME_VAR")


def test_require_raises_key_error_when_empty_string():
    with pytest.raises(KeyError, match="SOME_VAR"):
        require("", "SOME_VAR")


def test_validate_startup_passes_when_all_pairs_fully_unset():
    validate_startup(Settings())


def test_validate_startup_passes_when_all_pairs_fully_set():
    # Formes d'endpoint conformes à la règle projet : `/openai/v1` pour les
    # services OpenAI-compatibles, `/anthropic` pour Foundry.
    settings = Settings(
        azure_ai_inference_endpoint="https://example.services.ai.azure.com/openai/v1",
        azure_ai_inference_api_key="key",
        azure_openai_guard_endpoint="https://example.openai.azure.com/openai/v1",
        azure_openai_guard_api_key="key",
        anthropic_foundry_endpoint="https://example.services.ai.azure.com/anthropic",
        anthropic_api_key="key",
        azure_language_endpoint="https://example.com",
        azure_language_key="key",
        azure_content_safety_endpoint="https://example.com",
        azure_content_safety_key="key",
    )
    validate_startup(settings)


def test_validate_startup_raises_on_partial_pair_endpoint_only():
    settings = Settings(azure_openai_guard_endpoint="https://example.com")
    with pytest.raises(ConfigurationError, match="AZURE_OPENAI_GUARD_API_KEY"):
        validate_startup(settings)


def test_validate_startup_raises_on_partial_pair_key_only():
    settings = Settings(azure_ai_inference_api_key="key")
    with pytest.raises(ConfigurationError, match="AZURE_AI_INFERENCE_ENDPOINT"):
        validate_startup(settings)


def test_validate_startup_reports_every_partial_pair():
    settings = Settings(
        azure_openai_guard_endpoint="https://example.com",
        azure_language_key="key",
    )
    with pytest.raises(ConfigurationError) as exc_info:
        validate_startup(settings)
    message = str(exc_info.value)
    assert "AZURE_OPENAI_GUARD_API_KEY" in message
    assert "AZURE_LANGUAGE_ENDPOINT" in message


def test_validate_startup_rejects_placeholder_values():
    # Un `<resource>` copié de .env.example = service « posé » mais bidon :
    # chaque appel échouerait (étage garde-fous en panne permanente → fail-closed
    # global, constaté en réel). Erreur explicite au démarrage.
    settings = Settings(
        azure_content_safety_endpoint="https://<resource>.cognitiveservices.azure.com",
        azure_content_safety_key="une-vraie-cle",
    )
    with pytest.raises(ConfigurationError, match="placeholder"):
        validate_startup(settings)


def test_validate_startup_rejects_bare_openai_compatible_endpoint():
    # Le portail donne l'endpoint « nu » ; le client OpenAI standard exige la
    # forme /openai/v1 — copié tel quel, chaque appel 404 (constaté sur le juge).
    settings = Settings(
        azure_openai_guard_endpoint="https://example.openai.azure.com",
        azure_openai_guard_api_key="key",
    )
    with pytest.raises(ConfigurationError, match="openai/v1"):
        validate_startup(settings)


def test_validate_startup_rejects_foundry_endpoint_without_anthropic_suffix():
    settings = Settings(
        anthropic_foundry_endpoint="https://example.services.ai.azure.com",
        anthropic_api_key="key",
    )
    with pytest.raises(ConfigurationError, match="anthropic"):
        validate_startup(settings)


def test_validate_startup_tolerates_trailing_slash_on_suffix():
    settings = Settings(
        azure_ai_inference_endpoint="https://example.services.ai.azure.com/openai/v1/",
        azure_ai_inference_api_key="key",
    )
    validate_startup(settings)  # ne lève pas


def test_guardrail_classifier_backend_defaults_to_none():
    from velmo.config import Settings

    assert Settings().guardrail_classifier_backend is None


def test_guardrail_classifier_backend_reads_env(monkeypatch):
    from velmo.config import get_settings

    monkeypatch.setenv("GUARDRAIL_CLASSIFIER_BACKEND", "content_safety")
    assert get_settings().guardrail_classifier_backend == "content_safety"
