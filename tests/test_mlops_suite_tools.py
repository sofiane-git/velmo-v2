"""Suite Outils — Ch.4 §Évaluation (audit Z-01 / règle TOO-09).

La couche qui engage de l'argent était la seule sans métrique. Ces tests vérifient
la suite elle-même : qu'elle rejoue bien la fixture, et surtout que sa **note qui
gate** ne contient que du déterministe — sinon on ferait entrer du bruit de routage
dans le blocage d'une livraison (M4).
"""

from __future__ import annotations

from conftest import build_reference_agent

from velmo.mlops.results import CaseResult
from velmo.mlops.suites.tools import (
    GATING_KINDS,
    _load_cases,
    run_tools_suite,
    tools_scores,
)


def test_suite_replays_every_case() -> None:
    results = run_tools_suite(build_reference_agent)
    assert len(results) == len(_load_cases())
    assert {r.suite for r in results} == {"tools"}


def test_fixture_covers_the_three_kinds() -> None:
    """Une suite qui n'aurait que des cas de sélection ne gaterait rien ; une qui
    n'aurait que des refus ne mesurerait pas le routage."""
    kinds = {case["kind"] for case in _load_cases()}
    assert kinds == {"selection", "refusal", "confirmation"}


def test_every_gating_kind_has_at_least_two_cases() -> None:
    cases = _load_cases()
    for kind in GATING_KINDS:
        assert len([c for c in cases if c["kind"] == kind]) >= 2, kind


def test_deterministic_cases_all_pass_on_the_reference_agent() -> None:
    """Les refus et la confirmation sont des invariants du code, pas des
    jugements : sur l'agent de référence ils doivent **tous** passer, sinon c'est
    une régression réelle de la couche outils."""
    results = run_tools_suite(build_reference_agent)
    kinds = {case["id"]: case["kind"] for case in _load_cases()}
    failed = [r.case_id for r in results if kinds[r.case_id] in GATING_KINDS and not r.passed]
    assert failed == []


def test_note_tools_excludes_selection_cases() -> None:
    """Le point de conception vérifié ici : la sélection est publiée à part et
    n'entre pas dans la note qui gate."""
    kinds = {case["id"]: case["kind"] for case in _load_cases()}
    results = [
        CaseResult(
            case_id=case_id, suite="tools", passed=(kind != "selection"), score=0.0, latency_ms=0.0
        )
        for case_id, kind in kinds.items()
    ]
    note_tools, selection_accuracy = tools_scores(results)
    assert note_tools == 1.0  # tous les déterministes passent
    assert selection_accuracy == 0.0  # toutes les sélections échouent


def test_infra_failures_are_excluded_from_both_scores() -> None:
    """Un timeout n'est pas une régression de l'agent (Ch.3 §Robustesse)."""
    kinds = {case["id"]: case["kind"] for case in _load_cases()}
    gating_id = next(cid for cid, k in kinds.items() if k in GATING_KINDS)
    selection_id = next(cid for cid, k in kinds.items() if k == "selection")
    results = [
        CaseResult(
            case_id=gating_id,
            suite="tools",
            passed=False,
            score=0.0,
            latency_ms=0.0,
            error_kind="infra",
        ),
        CaseResult(
            case_id=selection_id,
            suite="tools",
            passed=False,
            score=0.0,
            latency_ms=0.0,
            error_kind="infra",
        ),
    ]
    assert tools_scores(results) == (0.0, 0.0)  # aucun cas compté -> pas de note


def test_selection_accuracy_is_reported_between_zero_and_one() -> None:
    results = run_tools_suite(build_reference_agent)
    _, selection_accuracy = tools_scores(results)
    assert 0.0 <= selection_accuracy <= 1.0
