from __future__ import annotations

import pytest
import requests

from velmo.guardrails._scoring import FALLBACK_MAX_SCORE
from velmo.guardrails.classifier import (
    ClassifierResult,
    CombinedClassifier,
    ContentSafetyClassifier,
    LexicalClassifier,
    LlamaGuardClassifier,
    _extract_p_unsafe,
    _parse_llama_guard_response,
    get_classifier,
)


class _FakeResponse:
    def __init__(self, content: str, logprobs: list[dict] | None = None) -> None:
        self._content = content
        self._logprobs = logprobs

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        body: dict = {"message": {"content": self._content}}
        if self._logprobs is not None:
            body["logprobs"] = self._logprobs
        return body


def test_llama_guard_classifier_uses_timeout_below_pipeline_call_timeout(monkeypatch):
    # < CALL_TIMEOUT_S (30s, pipeline.py) : sinon ce timeout HTTP n'a jamais
    # l'occasion de se déclencher avant que le pipeline n'abandonne l'attente
    # sans libérer le thread du pool partagé (cf. commentaires classifier.py
    # et judge.py).
    calls: list[dict] = []

    def fake_post(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(kwargs)
        return _FakeResponse("safe")

    monkeypatch.setattr(requests, "post", fake_post)

    LlamaGuardClassifier(base_url="http://localhost:11434").score_detailed("bonjour")

    assert calls[0]["timeout"] < 30
    assert calls[0]["timeout"] >= 20


def test_lexical_classifier_detects_hate():
    scores = LexicalClassifier().score(
        "Ces clients sont des sous-humains qui devraient disparaitre."
    )
    assert scores["hate"] >= 0.7


def test_lexical_classifier_detects_violence():
    scores = LexicalClassifier().score("Si mon maillot n'arrive pas je vais te frapper.")
    assert scores["violence"] >= 0.7


def test_lexical_classifier_detects_sexual():
    scores = LexicalClassifier().score("Envoie-moi du contenu sexuel explicite.")
    assert scores["sexual"] >= 0.7


def test_lexical_classifier_caps_match_at_fallback_max_score():
    scores = LexicalClassifier().score("Si mon maillot n'arrive pas je vais te frapper.")
    assert scores["violence"] == FALLBACK_MAX_SCORE


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


def test_combined_classifier_keeps_lexical_signal_when_primary_raises() -> None:
    from velmo.guardrails.classifier import ModerationClassifier

    class _BrokenPrimary(ModerationClassifier):
        def score(self, text: str) -> dict[str, float]:
            raise ConnectionError("ollama down")

        def score_detailed(self, text: str) -> ClassifierResult:
            raise ConnectionError("ollama down")

    combined = CombinedClassifier(primary=_BrokenPrimary(), lexical=LexicalClassifier())
    result = combined.score_detailed("je vais te frapper")
    assert result.scores["violence"] > 0


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


def _logprobs_for(
    token: str, alt_token: str, token_logprob: float, alt_logprob: float
) -> list[dict]:
    return [
        {
            "token": token,
            "logprob": token_logprob,
            "top_logprobs": [
                {"token": token, "logprob": token_logprob},
                {"token": alt_token, "logprob": alt_logprob},
            ],
        }
    ]


def test_extract_p_unsafe_computes_softmax_between_safe_and_unsafe():
    logprobs = _logprobs_for("unsafe", "safe", -0.05, -4.0)
    p = _extract_p_unsafe(logprobs)
    assert p is not None
    assert 0.95 < p < 1.0


def test_extract_p_unsafe_favors_safe_when_more_likely():
    logprobs = _logprobs_for("safe", "unsafe", -0.02, -5.0)
    p = _extract_p_unsafe(logprobs)
    assert p is not None
    assert p < 0.05


def test_extract_p_unsafe_returns_none_when_safe_missing_from_top_logprobs():
    logprobs = [
        {
            "token": "unsafe",
            "logprob": -0.01,
            "top_logprobs": [{"token": "unsafe", "logprob": -0.01}],
        }
    ]
    assert _extract_p_unsafe(logprobs) is None


def test_extract_p_unsafe_returns_none_without_logprobs():
    assert _extract_p_unsafe(None) is None
    assert _extract_p_unsafe([]) is None


def test_score_detailed_uses_computed_p_unsafe_from_logprobs(monkeypatch):
    calls: list[dict] = []
    logprobs = _logprobs_for("unsafe", "safe", -0.05, -4.0)

    def fake_post(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(kwargs)
        return _FakeResponse("unsafe\nS10", logprobs=logprobs)

    monkeypatch.setattr(requests, "post", fake_post)
    result = LlamaGuardClassifier(base_url="http://localhost:11434").score_detailed("texte")

    assert 0.95 < result.scores["hate"] < 1.0
    assert calls[0]["json"]["logprobs"] is True
    assert calls[0]["json"]["top_logprobs"] == 5


def test_score_detailed_falls_back_to_capped_score_when_logprobs_missing(monkeypatch):
    def fake_post(url: str, **kwargs: object) -> _FakeResponse:
        return _FakeResponse("unsafe\nS10")  # pas de clé "logprobs" dans la réponse

    monkeypatch.setattr(requests, "post", fake_post)
    result = LlamaGuardClassifier(base_url="http://localhost:11434").score_detailed("texte")

    assert result.scores["hate"] == FALLBACK_MAX_SCORE


def test_llama_guard_records_last_latency(monkeypatch) -> None:
    def _fake_post(*args, **kwargs):
        return _FakeResponse("safe")

    monkeypatch.setattr(requests, "post", _fake_post)
    clf = LlamaGuardClassifier(base_url="http://fake-ollama:11434")
    assert clf.last_latency_ms is None
    clf.score_detailed("bonjour")
    assert clf.last_latency_ms is not None
    assert clf.last_latency_ms >= 0


def test_llama_guard_logs_warning_above_latency_threshold(monkeypatch, caplog) -> None:
    import logging
    import time

    def _slow_post(*args, **kwargs):
        time.sleep(0.05)
        return _FakeResponse("safe")

    monkeypatch.setattr(requests, "post", _slow_post)
    clf = LlamaGuardClassifier(base_url="http://fake-ollama:11434", latency_threshold_ms=10.0)
    with caplog.at_level(logging.WARNING, logger="velmo.guardrails.classifier"):
        clf.score_detailed("bonjour")
    assert any("latence" in r.message.lower() for r in caplog.records)


class _FakeCategoryAnalysis:
    def __init__(self, category: str, severity: int) -> None:
        self.category = category
        self.severity = severity


class _FakeAnalyzeResponse:
    def __init__(self, categories_analysis: list[_FakeCategoryAnalysis]) -> None:
        self.categories_analysis = categories_analysis


def test_content_safety_classifier_maps_hate_severity_to_score(monkeypatch):
    from azure.ai.contentsafety import ContentSafetyClient

    def fake_analyze_text(self, options):
        return _FakeAnalyzeResponse([_FakeCategoryAnalysis("Hate", 4)])

    monkeypatch.setattr(ContentSafetyClient, "analyze_text", fake_analyze_text)

    clf = ContentSafetyClassifier(endpoint="https://example.cognitiveservices.azure.com", key="k")
    scores = clf.score("peu importe")
    assert scores == {"hate": 0.8, "violence": 0.0, "sexual": 0.0}


def test_content_safety_classifier_maps_self_harm_to_violence(monkeypatch):
    from azure.ai.contentsafety import ContentSafetyClient

    def fake_analyze_text(self, options):
        return _FakeAnalyzeResponse([_FakeCategoryAnalysis("SelfHarm", 6)])

    monkeypatch.setattr(ContentSafetyClient, "analyze_text", fake_analyze_text)

    clf = ContentSafetyClassifier(endpoint="https://example.cognitiveservices.azure.com", key="k")
    result = clf.score_detailed("peu importe")
    assert result.scores == {"hate": 0.0, "violence": 0.95, "sexual": 0.0}
    assert result.reasoning["violence"] == "Content Safety : sévérité 6 (SelfHarm)"


def test_content_safety_classifier_zero_severity_gives_zero_score(monkeypatch):
    from azure.ai.contentsafety import ContentSafetyClient

    def fake_analyze_text(self, options):
        return _FakeAnalyzeResponse(
            [
                _FakeCategoryAnalysis("Hate", 0),
                _FakeCategoryAnalysis("Violence", 0),
                _FakeCategoryAnalysis("SelfHarm", 0),
                _FakeCategoryAnalysis("Sexual", 0),
            ]
        )

    monkeypatch.setattr(ContentSafetyClient, "analyze_text", fake_analyze_text)

    clf = ContentSafetyClassifier(endpoint="https://example.cognitiveservices.azure.com", key="k")
    result = clf.score_detailed("Comment retourner un maillot ?")
    assert result.scores == {"hate": 0.0, "violence": 0.0, "sexual": 0.0}
    assert result.reasoning == {}


def test_content_safety_classifier_keeps_max_when_violence_and_self_harm_both_hit(monkeypatch):
    from azure.ai.contentsafety import ContentSafetyClient

    def fake_analyze_text(self, options):
        return _FakeAnalyzeResponse(
            [
                _FakeCategoryAnalysis("Violence", 2),
                _FakeCategoryAnalysis("SelfHarm", 6),
            ]
        )

    monkeypatch.setattr(ContentSafetyClient, "analyze_text", fake_analyze_text)

    clf = ContentSafetyClassifier(endpoint="https://example.cognitiveservices.azure.com", key="k")
    scores = clf.score("peu importe")
    assert scores["violence"] == 0.95  # le pire des deux signaux, pas le dernier lu


def test_content_safety_classifier_requires_endpoint():
    with pytest.raises(KeyError):
        ContentSafetyClassifier(endpoint=None, key="k")


def test_content_safety_classifier_requires_key():
    with pytest.raises(KeyError):
        ContentSafetyClassifier(endpoint="https://example.cognitiveservices.azure.com", key=None)


def test_get_classifier_explicit_content_safety_raises_when_unconfigured(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_CLASSIFIER_BACKEND", "content_safety")
    monkeypatch.delenv("AZURE_CONTENT_SAFETY_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_CONTENT_SAFETY_KEY", raising=False)
    with pytest.raises(KeyError):
        get_classifier()


def test_get_classifier_explicit_llama_guard_raises_when_unconfigured(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_CLASSIFIER_BACKEND", "llama_guard")
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    with pytest.raises(KeyError):
        get_classifier()


def test_get_classifier_explicit_unknown_backend_raises_value_error(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_CLASSIFIER_BACKEND", "made_up_backend")
    with pytest.raises(ValueError):
        get_classifier()


def test_get_classifier_explicit_content_safety_returns_combined(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_CLASSIFIER_BACKEND", "content_safety")
    monkeypatch.setenv(
        "AZURE_CONTENT_SAFETY_ENDPOINT", "https://example.cognitiveservices.azure.com"
    )
    monkeypatch.setenv("AZURE_CONTENT_SAFETY_KEY", "k")
    assert isinstance(get_classifier(), CombinedClassifier)


def test_get_classifier_auto_detects_content_safety_over_llama_guard(monkeypatch):
    monkeypatch.delenv("GUARDRAIL_CLASSIFIER_BACKEND", raising=False)
    monkeypatch.setenv(
        "AZURE_CONTENT_SAFETY_ENDPOINT", "https://example.cognitiveservices.azure.com"
    )
    monkeypatch.setenv("AZURE_CONTENT_SAFETY_KEY", "k")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    classifier = get_classifier()
    assert isinstance(classifier, CombinedClassifier)
    assert isinstance(classifier._primary, ContentSafetyClassifier)


def test_get_classifier_auto_detects_llama_guard_without_content_safety(monkeypatch):
    monkeypatch.delenv("GUARDRAIL_CLASSIFIER_BACKEND", raising=False)
    monkeypatch.delenv("AZURE_CONTENT_SAFETY_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_CONTENT_SAFETY_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    classifier = get_classifier()
    assert isinstance(classifier, CombinedClassifier)
    assert isinstance(classifier._primary, LlamaGuardClassifier)
