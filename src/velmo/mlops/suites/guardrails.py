"""Suite Garde-fous : rejoue `eval/guardrail_cases.jsonl` contre
`GuardrailEngine` (API publique du Chantier 2 uniquement). Matrice de
confusion (positif = « bloqué » — `action != "allow"`, ce qui couvre aussi
`"filter"` depuis la distinction block/filter introduite au Chantier 2) —
voir conception_chantier3_evaluation_mlops.md §Définitions formelles des
métriques. 1 retry sur un cas en échec (`with_retry`, Task 2) ; latence
décomposée par composant via `sink` (Task 5).
"""

from __future__ import annotations

import functools
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from velmo.config import get_settings
from velmo.guardrails import GuardrailEngine
from velmo.guardrails.classifier import get_classifier
from velmo.guardrails.judge import get_judge
from velmo.mlops.observability import (
    InstrumentedClassifier,
    InstrumentedJudge,
    NullSink,
    ObservabilitySink,
)
from velmo.mlops.results import CaseResult, CaseStepEvent, with_retry

EVAL_PATH = Path(__file__).resolve().parents[4] / "eval" / "guardrail_cases.jsonl"


def _load_cases() -> list[dict[str, Any]]:
    text = EVAL_PATH.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _build_engine(db_url: str | None, sink: ObservabilitySink) -> GuardrailEngine:
    """Assemble un `GuardrailEngine` avec classifieur/juge instrumentés
    (composition pure, cf. Task 5 — aucun changement de `GuardrailEngine`)."""
    settings = get_settings()
    return GuardrailEngine(
        db_url=db_url,
        classifier=InstrumentedClassifier(get_classifier(), sink, "guardrails_classifier"),
        judge=InstrumentedJudge(
            get_judge(), sink, "guardrails_judge", settings.azure_openai_guard_deployment
        ),
    )


def _run_one_case(case: dict[str, Any], engine: GuardrailEngine) -> CaseResult:
    start = time.monotonic()
    try:
        where = case["where"]
        if where == "input":
            decision = engine.check_input(case["message"], user_id=case["user_id"])
        elif where == "retrieved":
            # G8 : contenu récupéré (extrait de FAQ, champ métier). Écarter
            # l'extrait compte comme « bloqué » au sens de la matrice de
            # confusion — c'est bien un contenu qui n'atteint pas l'agent.
            decision = engine.check_retrieved(case["message"], source=case.get("source", "faq"))
        elif where == "memory_write":
            # G8 : écriture mémoire candidate, fail-closed.
            decision = engine.check_memory_write(
                case["message"], kind=case.get("kind", "procedure")
            )
        else:
            decision = engine.check_output(case["message"], user_id=case["user_id"])
        actually_blocked = decision.action != "allow"
        expected_blocked = case["expected_action"] == "block"
        passed = actually_blocked == expected_blocked
        if passed and case.get("expected_escalate"):
            passed = decision.escalate is True
        return CaseResult(
            case_id=case["id"],
            suite="guardrails",
            passed=passed,
            score=1.0 if passed else 0.0,
            latency_ms=(time.monotonic() - start) * 1000,
        )
    except Exception:
        return CaseResult(
            case_id=case["id"],
            suite="guardrails",
            passed=False,
            score=0.0,
            latency_ms=(time.monotonic() - start) * 1000,
            error_kind="infra",
        )


def run_guardrails_suite_steps(
    db_url: str | None = None, sink: ObservabilitySink | None = None
) -> Iterator[CaseStepEvent]:
    """Version générateur de `run_guardrails_suite` — diffuse un
    `CaseStepEvent` `"start"` avant chaque cas puis `"done"` une fois son
    `CaseResult` connu (même rôle que `run_memory_suite_steps`), pour que
    `run_eval_steps` streame la progression cas par cas."""
    sink = sink or NullSink()
    engine = _build_engine(db_url, sink)
    for case in _load_cases():
        yield CaseStepEvent("start", case["id"])
        result = with_retry(functools.partial(_run_one_case, case, engine))
        yield CaseStepEvent("done", case["id"], result)


def run_guardrails_suite(
    db_url: str | None = None, sink: ObservabilitySink | None = None
) -> list[CaseResult]:
    """`db_url=None` est transmis tel quel à `GuardrailEngine` : c'est
    `make_guardrails_engine` (Chantier 2) qui résout alors `Settings.db_url` —
    même principe que `run_memory_suite`, ne pas forcer un `:memory:` par
    défaut qui ignorerait la base configurée en prod/CI réelle. `sink=None`
    retombe sur `NullSink` (comportement historique, pas d'instrumentation).
    Un même `GuardrailEngine` (donc un même classifieur/juge) est réutilisé
    pour tous les cas — les cas garde-fous sont indépendants par construction
    (pas d'état partagé entre `user_id`, contrairement à R3 côté mémoire),
    réutiliser l'engine évite juste de reconstruire classifieur/juge à chaque
    cas."""
    results: list[CaseResult] = []
    for event in run_guardrails_suite_steps(db_url=db_url, sink=sink):
        if event.kind == "done":
            assert event.result is not None
            results.append(event.result)
    return results


def guardrails_confusion_matrix(results: list[CaseResult]) -> tuple[float, float]:
    """Rappel (TP / (TP+FN) sur les cas malveillants) et taux de faux positifs
    (FP / (FP+TN) sur les cas légitimes) — nécessite de recharger les cas
    (catégorie `legitimate` ou non) pour classer chaque résultat."""
    cases_by_id = {c["id"]: c for c in _load_cases()}
    malicious_total = malicious_correct = 0
    legitimate_total = legitimate_false_positive = 0
    for result in results:
        case = cases_by_id[result.case_id]
        if case["category"] == "legitimate":
            legitimate_total += 1
            if not result.passed:
                legitimate_false_positive += 1
        else:
            malicious_total += 1
            if result.passed:
                malicious_correct += 1
    recall = malicious_correct / malicious_total if malicious_total else 1.0
    fpr = legitimate_false_positive / legitimate_total if legitimate_total else 0.0
    return recall, fpr
