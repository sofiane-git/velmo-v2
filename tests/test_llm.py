"""Contrat de démarrage : EchoLLM ne doit jamais être un repli silencieux en prod."""

from __future__ import annotations

import logging

import pytest

from velmo.config import Settings
from velmo.llm import EchoLLM, get_llm


def test_get_llm_raises_in_production_without_azure_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("AZURE_AI_INFERENCE_ENDPOINT", raising=False)
    with pytest.raises(RuntimeError, match="AZURE_AI_INFERENCE_ENDPOINT"):
        get_llm()


def test_get_llm_falls_back_to_echo_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("AZURE_AI_INFERENCE_ENDPOINT", raising=False)
    llm = get_llm()
    assert isinstance(llm, EchoLLM)


def test_echo_llm_logs_warning_on_instantiation(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="velmo.llm"):
        EchoLLM()
    assert any("EchoLLM" in record.message for record in caplog.records)


def test_settings_default_environment_is_development() -> None:
    assert Settings().environment == "development"


def test_azure_openai_llm_implements_llm_protocol() -> None:
    from velmo.llm import AzureOpenAILLM, LLM

    llm = AzureOpenAILLM(endpoint="https://fake.openai.azure.com", api_key="fake", deployment="gpt-5-mini")
    assert isinstance(llm, LLM)
