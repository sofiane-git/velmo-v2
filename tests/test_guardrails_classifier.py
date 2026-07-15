from __future__ import annotations

from velmo.guardrails.classifier import (
    ClassifierResult,
    CombinedClassifier,
    LexicalClassifier,
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


def test_get_classifier_uses_combined_when_configured(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    assert isinstance(get_classifier(), CombinedClassifier)


class _StubClassifier:
    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores

    def score(self, text: str) -> dict[str, float]:
        return self._scores

    def score_detailed(self, text: str) -> ClassifierResult:
        return ClassifierResult(scores=self._scores, reasoning={})


def test_combined_classifier_takes_max_per_category():
    primary = _StubClassifier({"hate": 0.0, "violence": 1.0, "sexual": 0.0})
    lexical = _StubClassifier({"hate": 1.0, "violence": 0.0, "sexual": 0.0})
    combined = CombinedClassifier(primary, lexical)  # type: ignore[arg-type]
    assert combined.score("peu importe") == {"hate": 1.0, "violence": 1.0, "sexual": 0.0}


def test_combined_classifier_zero_when_both_zero():
    primary = _StubClassifier({"hate": 0.0, "violence": 0.0, "sexual": 0.0})
    lexical = _StubClassifier({"hate": 0.0, "violence": 0.0, "sexual": 0.0})
    combined = CombinedClassifier(primary, lexical)  # type: ignore[arg-type]
    assert combined.score("peu importe") == {"hate": 0.0, "violence": 0.0, "sexual": 0.0}


def test_combined_classifier_defaults_lexical_when_not_provided():
    primary = _StubClassifier({"hate": 0.0, "violence": 0.0, "sexual": 0.0})
    combined = CombinedClassifier(primary)  # type: ignore[arg-type]
    scores = combined.score("Si mon maillot n'arrive pas je vais te frapper.")
    assert scores["violence"] >= 0.7


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


def test_lexical_classifier_score_detailed_gives_matched_phrase():
    result = LexicalClassifier().score_detailed("Si mon maillot n'arrive pas je vais te frapper.")
    assert result.scores["violence"] >= 0.7
    assert result.reasoning["violence"] == "Expression détectée : « frapper »"


def test_lexical_classifier_score_detailed_no_reasoning_when_clean():
    result = LexicalClassifier().score_detailed("Comment retourner un maillot qui ne me va pas ?")
    assert result.reasoning == {}


def test_lexical_classifier_score_matches_score_detailed_scores():
    text = "Ces clients sont des sous-humains qui devraient disparaitre."
    classifier = LexicalClassifier()
    assert classifier.score(text) == classifier.score_detailed(text).scores


def test_combined_classifier_score_detailed_prefers_primary_reasoning_on_tie():
    primary = _StubClassifier({"hate": 0.0, "violence": 1.0, "sexual": 0.0})
    primary_detailed = ClassifierResult(
        scores={"hate": 0.0, "violence": 1.0, "sexual": 0.0},
        reasoning={"violence": "Llama Guard 3 : unsafe (S1)"},
    )
    primary.score_detailed = lambda text: primary_detailed  # type: ignore[method-assign]
    combined = CombinedClassifier(primary, LexicalClassifier())  # type: ignore[arg-type]
    result = combined.score_detailed("je vais te frapper")
    assert result.scores["violence"] == 1.0
    assert result.reasoning["violence"] == "Llama Guard 3 : unsafe (S1)"
