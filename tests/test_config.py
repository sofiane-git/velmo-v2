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
    settings = Settings(
        azure_ai_inference_endpoint="https://example.com",
        azure_ai_inference_api_key="key",
        azure_openai_guard_endpoint="https://example.com",
        azure_openai_guard_api_key="key",
        anthropic_foundry_endpoint="https://example.com",
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
