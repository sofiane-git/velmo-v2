"""Alerte deux-nuits-consécutives sur les dimensions du gate (audit D8-04).

Conception ch.3 §Rollback : « si une dimension gate passe sous son seuil deux
nuits consécutives (le filtre anti-bruit : une seule nuit peut être un aléa),
une alerte est levée → décision humaine de rollback ». Ce module implémente le
filtre ; le canal d'alerte est l'échec du job nightly lui-même (notification
GitHub par défaut) — voir le step « Alerte deux-nuits » de nightly.yml.
"""

from __future__ import annotations

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from velmo.config import get_settings
from velmo.mlops.db import EvalRun, make_mlops_engine

_GATE_DIMENSIONS = ("memory", "guardrails", "quality")
# Seuls les runs de surveillance planifiée comptent comme « nuits » : un run
# release (`ci`) ou hotfix intercalé ne doit pas se faire passer pour une nuit
# et fausser la règle deux-nuits (revue Lot 3).
_NIGHTLY_SOURCES = ("nightly", "model-drift")


def consecutive_breaches(session: Session, min_score: float) -> list[str]:
    """Dimensions gate sous `min_score` sur les DEUX derniers runs de
    surveillance planifiée (triggered_by nightly/model-drift).

    Moins de deux runs en base → aucune alerte possible (pas assez
    d'historique pour distinguer une tendance d'un aléa)."""
    runs = list(
        session.scalars(
            select(EvalRun)
            .where(EvalRun.triggered_by.in_(_NIGHTLY_SOURCES))
            .order_by(EvalRun.ran_at.desc())
            .limit(2)
        ).all()
    )
    if len(runs) < 2:
        return []
    latest, previous = runs[0], runs[1]
    breaches: list[str] = []
    for dim in _GATE_DIMENSIONS:
        latest_note = getattr(latest, f"note_{dim}")
        previous_note = getattr(previous, f"note_{dim}")
        if latest_note < min_score and previous_note < min_score:
            breaches.append(dim)
    return breaches


def main() -> None:
    settings = get_settings()
    engine = make_mlops_engine()
    session = sessionmaker(bind=engine, future=True)()
    try:
        breaches = consecutive_breaches(session, settings.gate_min_score)
    finally:
        session.close()

    if breaches:
        print(
            "ALERTE deux-nuits : dimension(s) sous plancher deux runs consécutifs : "
            + ", ".join(breaches)
            + " — décision humaine de rollback requise (conception ch.3 §Rollback).",
            file=sys.stderr,
        )
        sys.exit(1)
    print("Aucune dérive deux-nuits sur les dimensions gate.")


if __name__ == "__main__":
    main()
