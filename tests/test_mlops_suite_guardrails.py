from __future__ import annotations

from velmo.mlops.suites.guardrails import guardrails_confusion_matrix, run_guardrails_suite


def test_guardrails_suite_covers_all_cases() -> None:
    results = run_guardrails_suite(db_url="sqlite:///:memory:")
    assert len(results) == 46  # cf. wc -l eval/guardrail_cases.jsonl — revérifier à
    # chaque ajout de fixture (la Task ajoute rarement des cas sans qu'un humain les
    # revoie, cf. conception §Gouvernance des fixtures : "tout ajout/modif de fixture
    # passe en PR").


def test_guardrails_suite_recall_and_fpr_reasonable() -> None:
    """Repli hors-ligne (LexicalClassifier/RuleBasedJudge, pas de service
    cloud/Ollama configuré en test) : le rappel exact dépend du repli, mais le
    calcul rappel/FPR doit être exécutable et dans [0, 1]."""
    results = run_guardrails_suite(db_url="sqlite:///:memory:")
    recall, fpr = guardrails_confusion_matrix(results)
    assert 0.0 <= recall <= 1.0
    assert 0.0 <= fpr <= 1.0


def test_guardrails_suite_emits_instrumentation_events() -> None:
    class _RecordingSink:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, float, float]] = []

        def on_llm_call(
            self,
            component: str,
            tokens: int,
            latency_ms: float,
            cost: float,
            *,
            input: str | None = None,
            output: str | None = None,
            model: str | None = None,
        ) -> None:
            self.calls.append((component, tokens, latency_ms, cost))

        def run_url(self, run_id: str) -> str | None:
            return None

    sink = _RecordingSink()
    run_guardrails_suite(db_url="sqlite:///:memory:", sink=sink)
    assert len(sink.calls) > 0
    assert all(c[0] in ("guardrails_classifier", "guardrails_judge") for c in sink.calls)
