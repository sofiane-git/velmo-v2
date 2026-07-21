from __future__ import annotations

from conftest import build_reference_agent

from velmo.mlops.suites.quality import SubstringScorer, get_quality_scorer, run_quality_suite


def test_substring_scorer_scores_one_when_present() -> None:
    scorer = SubstringScorer()
    assert scorer.score("q", "la livraison prend J+2 ouvrés", "J+2") == 1.0


def test_substring_scorer_scores_zero_when_absent() -> None:
    scorer = SubstringScorer()
    assert scorer.score("q", "aucune information", "J+2") == 0.0


def test_get_quality_scorer_falls_back_to_substring_without_anthropic_foundry(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_ENDPOINT", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    scorer = get_quality_scorer()
    assert isinstance(scorer, SubstringScorer)


def test_run_quality_suite_covers_all_cases() -> None:
    agent = build_reference_agent()
    results = run_quality_suite(agent, db_url="sqlite:///:memory:")
    assert len(results) == 8  # cf. wc -l eval/quality_cases.jsonl
