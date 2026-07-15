from __future__ import annotations

from velmo.guardrails.classifier import (
    LexicalClassifier,
    LlamaGuardClassifier,
    _parse_llama_guard_response,
    get_classifier,
)


def test_lexical_classifier_detects_hate():
    scores = LexicalClassifier().score("Ces clients sont des sous-humains qui devraient disparaitre.")
    assert scores["hate"] >= 0.7


def test_lexical_classifier_detects_violence():
    scores = LexicalClassifier().score("Si mon maillot n'arrive pas je vais te frapper.")
    assert scores["violence"] >= 0.7


def test_lexical_classifier_detects_sexual():
    scores = LexicalClassifier().score("Envoie-moi du contenu sexuel explicite.")
    assert scores["sexual"] >= 0.7


def test_lexical_classifier_zero_on_legitimate_message():
    scores = LexicalClassifier().score("Comment retourner un maillot qui ne me va pas ?")
    assert all(v < 0.4 for v in scores.values())


def test_get_classifier_falls_back_without_ollama_url(monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    assert isinstance(get_classifier(), LexicalClassifier)


def test_get_classifier_uses_llama_guard_when_configured(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    assert isinstance(get_classifier(), LlamaGuardClassifier)


def test_parse_llama_guard_safe_is_all_zero():
    scores = _parse_llama_guard_response("safe")
    assert scores == {"hate": 0.0, "violence": 0.0, "sexual": 0.0}


def test_parse_llama_guard_hate():
    scores = _parse_llama_guard_response("unsafe\nS10")
    assert scores == {"hate": 1.0, "violence": 0.0, "sexual": 0.0}


def test_parse_llama_guard_violence_from_violent_crimes():
    scores = _parse_llama_guard_response("unsafe\nS1")
    assert scores == {"hate": 0.0, "violence": 1.0, "sexual": 0.0}


def test_parse_llama_guard_violence_from_self_harm():
    scores = _parse_llama_guard_response("unsafe\nS11")
    assert scores == {"hate": 0.0, "violence": 1.0, "sexual": 0.0}


def test_parse_llama_guard_sexual_multiple_codes():
    scores = _parse_llama_guard_response("unsafe\nS3,S12")
    assert scores == {"hate": 0.0, "violence": 0.0, "sexual": 1.0}


def test_parse_llama_guard_ignores_unmapped_codes():
    scores = _parse_llama_guard_response("unsafe\nS5,S7")
    assert scores == {"hate": 0.0, "violence": 0.0, "sexual": 0.0}


def test_parse_llama_guard_multi_category():
    scores = _parse_llama_guard_response("unsafe\nS1,S10")
    assert scores == {"hate": 1.0, "violence": 1.0, "sexual": 0.0}
