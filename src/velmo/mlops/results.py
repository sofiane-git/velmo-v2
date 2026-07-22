"""Type de résultat partagé par les 3 suites — `run_eval` (mlops/__init__.py)
les agrège en `Scores` et les persiste en `EvalCaseResult`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal


@dataclass
class CaseResult:
    case_id: str
    suite: str  # "memory" | "guardrails" | "quality"
    passed: bool
    score: float  # 0.0 ou 1.0 pour un cas binaire déterministe ; continu pour Qualité
    latency_ms: float
    error_kind: str | None = None  # "infra" | "agent" | None
    retried: bool = False


@dataclass(frozen=True)
class CaseStepEvent:
    """Un pas des variantes `run_*_suite_steps` (mémoire/garde-fous/qualité) :
    un cas qui démarre (`kind="start"`, `result=None`) ou un cas terminé
    (`kind="done"`, `result` renseigné) — même rôle que `GateEvent`
    (`mlops/__init__.py`) mais un niveau plus bas (le cas, pas la suite).
    Permet à `run_eval_steps` de diffuser la progression cas par cas."""

    kind: Literal["start", "done"]
    case_id: str
    result: CaseResult | None = None


def with_retry(run_once: Callable[[], CaseResult]) -> CaseResult:
    """1 retry max sur un cas d'abord en échec (mémoire/garde-fous, tous deux
    déterministes) — absorbe un flake isolé sans jamais masquer une vraie
    régression (le 2ᵉ résultat, réussi ou non, est celui qui compte ;
    `retried=True` trace qu'un essai supplémentaire a eu lieu, cf.
    conception_chantier3_evaluation_mlops.md §Éviter de bloquer pour du bruit).
    La Suite Qualité n'utilise **pas** ce helper : son anti-bruit est
    statistique (delta vs baseline, `stats.non_regression_ok`, Task 4/6), pas
    un retry par cas."""
    first = run_once()
    if first.passed:
        return first
    second = run_once()
    return CaseResult(
        case_id=second.case_id,
        suite=second.suite,
        passed=second.passed,
        score=second.score,
        latency_ms=first.latency_ms + second.latency_ms,
        error_kind=second.error_kind,
        retried=True,
    )
