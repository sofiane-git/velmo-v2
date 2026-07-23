from __future__ import annotations

from velmo.guardrails import pii_redaction


def test_scan_returns_none_without_config(monkeypatch):
    # `None` (non `[]`) : distingue « service non configuré » d'« aucune PII »
    # pour que le pipeline n'applique le repli G4 que sur une vraie panne (D4-05).
    monkeypatch.delenv("AZURE_LANGUAGE_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_LANGUAGE_KEY", raising=False)
    assert pii_redaction.scan("un texte quelconque") is None


def test_redact_spans_masks_multiple_offsets():
    text = "Client Marie Curie, contact Paul Durand."
    spans = [
        (text.index("Marie Curie"), len("Marie Curie")),
        (text.index("Paul Durand"), len("Paul Durand")),
    ]
    out = pii_redaction.redact_spans(text, spans)
    assert "Marie Curie" not in out
    assert "Paul Durand" not in out
