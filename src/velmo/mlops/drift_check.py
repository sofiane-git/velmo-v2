"""Vérification ciblée post-drift modèle : rejoue seulement la/les suite(s)
concernée(s) par un changement de version de déploiement Azure (détecté par
le job `check-model-drift` du workflow nightly), sans passer par le gate de
livraison (`run_eval`/`EvalRun`) — ce gate suppose les 3 suites présentes
(`note_memory`/`note_guardrails`/`note_quality` NOT NULL en base) et un run
partiel ne peut pas s'y persister sans fausser une des notes manquantes. Ce
module reste volontairement séparé de `mlops/cli.py` (le CLI du gate CI) :
un run de diagnostic post-drift n'est pas une décision de livraison."""

from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass
from typing import Literal

from velmo.mlops.observability import CostAccumulatingSink, NullSink, ObservabilitySink
from velmo.mlops.results import CaseResult
from velmo.mlops.runner import build_gate_agent
from velmo.mlops.suites.guardrails import guardrails_confusion_matrix, run_guardrails_suite
from velmo.mlops.suites.memory import run_memory_suite
from velmo.mlops.suites.quality import run_quality_suite

Suite = Literal["memory", "guardrails", "quality"]
ALL_SUITES: tuple[Suite, ...] = ("memory", "guardrails", "quality")


@dataclass(frozen=True)
class DriftCheckResult:
    suite: Suite
    cases: int
    passed: int
    note: float


def _pass_rate_note(suite: Suite, results: list[CaseResult]) -> DriftCheckResult:
    passed = sum(1 for r in results if r.passed)
    scores = [r.score for r in results]
    note = statistics.mean(scores) if scores else 0.0
    return DriftCheckResult(suite, len(results), passed, note)


def run_drift_check(
    suites: list[Suite],
    *,
    db_url: str | None = None,
    sink: ObservabilitySink | None = None,
) -> list[DriftCheckResult]:
    """Rejoue uniquement les suites passées dans `suites` (sous-ensemble de
    `ALL_SUITES`) — pas de note globale, pas de persistance `EvalRun` : ce
    n'est pas le gate, juste une vérification de non-régression ciblée."""
    sink = sink or NullSink()
    cost_sink = sink if isinstance(sink, CostAccumulatingSink) else CostAccumulatingSink(sink)
    results: list[DriftCheckResult] = []

    if "memory" in suites:
        results.append(_pass_rate_note("memory", run_memory_suite(db_url=db_url, sink=cost_sink)))

    if "guardrails" in suites:
        case_results = run_guardrails_suite(db_url=db_url, sink=cost_sink)
        recall, false_positive_rate = guardrails_confusion_matrix(case_results)
        note = 0.6 * recall + 0.4 * (1 - false_positive_rate)
        passed = sum(1 for r in case_results if r.passed)
        results.append(DriftCheckResult("guardrails", len(case_results), passed, note))

    if "quality" in suites:
        agent = build_gate_agent(cost_sink)
        results.append(_pass_rate_note("quality", run_quality_suite(agent, db_url=db_url)))

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vérification ciblée post-drift modèle (hors gate de livraison)"
    )
    parser.add_argument(
        "--suites",
        required=True,
        help="Suites à rejouer, séparées par des virgules (memory,guardrails,quality)",
    )
    parser.add_argument("--triggered-by", default="model-drift")
    args = parser.parse_args()

    requested = [s.strip() for s in args.suites.split(",") if s.strip()]
    invalid = set(requested) - set(ALL_SUITES)
    if invalid:
        raise SystemExit(f"Suite(s) inconnue(s) : {', '.join(sorted(invalid))}")

    from velmo.mlops.observability import get_sink

    raw_sink = get_sink()
    results = run_drift_check(requested, sink=raw_sink)

    close = getattr(raw_sink, "close", None)
    if close is not None:
        close()

    for result in results:
        print(f"{result.suite}: {result.passed}/{result.cases} — note {result.note:.2%}")

    sys.exit(0)


if __name__ == "__main__":
    main()
