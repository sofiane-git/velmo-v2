from __future__ import annotations

from velmo.guardrails import prompt_shields


def test_check_returns_none_without_config(monkeypatch):
    monkeypatch.delenv("AZURE_CONTENT_SAFETY_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_CONTENT_SAFETY_KEY", raising=False)
    assert prompt_shields.check("un texte quelconque") is None
