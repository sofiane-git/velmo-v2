from __future__ import annotations

from velmo.guardrails import pii_redaction


def test_scan_returns_empty_without_config(monkeypatch):
    monkeypatch.delenv("AZURE_LANGUAGE_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_LANGUAGE_KEY", raising=False)
    assert pii_redaction.scan("un texte quelconque") == []
