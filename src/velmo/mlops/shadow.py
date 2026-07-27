"""Agrégat de divergence du `RuleBasedJudge` en shadow mode — audit O-02.

Le Ch.2 impose au repli déterministe de tourner « sur chaque message, en
continu », son verdict « loggué et comparé » au verdict du juge cloud, dans le but
explicite de **calibrer le repli avant** qu'une panne ne l'appelle. La colonne
`guardrail_audit.shadow_verdict` était bien écrite — et jamais relue : aucun
agrégat, aucun champ de rapport, donc aucune calibration possible en pratique. Un
chemin de repli mesuré mais non lu n'est pas plus exercé qu'un chemin mort.

Ce module ferme la boucle : il lit ce que le shadow a produit et en tire un taux
publiable.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from velmo.guardrails.db import GuardrailAudit

logger = logging.getLogger(__name__)

# Au-delà, le repli est jugé mal calibré et doit être révisé avant la prochaine
# version. **Non bloquant** : la divergence mesure la qualité d'un chemin
# dégradé, pas une régression du service rendu.
DIVERGENCE_ATTENTION_THRESHOLD = 0.20

# Seuil au-dessus duquel un score de shadow est considéré comme « le repli aurait
# aussi déclenché ». Aligné sur `pipeline.BLOCK_THRESHOLD` : comparer un verdict
# de blocage à un score brut demanderait sinon deux conventions différentes.
_SHADOW_TRIGGER_SCORE = 0.7


def _shadow_would_have_triggered(raw: str) -> bool | None:
    """`True`/`False` selon que le verdict shadow aurait déclenché, `None` si le
    verdict est illisible (on ne compte pas un JSON cassé comme un accord)."""
    try:
        verdict = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(verdict, dict):
        return None
    scores = [v for v in verdict.values() if isinstance(v, (int, float))]
    if not scores:
        return None
    return max(scores) >= _SHADOW_TRIGGER_SCORE


def shadow_divergence_rate(session: Session, category: str | None = None) -> float | None:
    """Proportion des hits du juge cloud où le verdict shadow le **contredit**.

    Ne compte que les lignes **comparables** : celles qui portent un
    `shadow_verdict` lisible. Un hit `regex` ou `classifier` n'a pas de shadow —
    le compter comme un accord gonflerait artificiellement la qualité du repli.

    Renvoie `None` s'il n'existe aucune ligne comparable. C'est volontairement
    distinct de `0.0` : publier zéro se lirait comme « repli parfaitement
    calibré » là où la vérité est « repli jamais exercé ».
    """
    stmt = select(GuardrailAudit).where(GuardrailAudit.shadow_verdict.is_not(None))
    if category is not None:
        stmt = stmt.where(GuardrailAudit.category == category)

    comparable = 0
    divergent = 0
    for row in session.scalars(stmt):
        shadow_triggered = _shadow_would_have_triggered(row.shadow_verdict or "")
        if shadow_triggered is None:
            continue
        comparable += 1
        cloud_triggered = row.action in ("block", "block_escalate")
        if shadow_triggered != cloud_triggered:
            divergent += 1

    if comparable == 0:
        return None
    rate = divergent / comparable
    if rate > DIVERGENCE_ATTENTION_THRESHOLD:
        logger.warning(
            "Repli garde-fous mal calibré : divergence shadow %.1f%% (> %.0f%%) sur %d hits — "
            "à réviser avant la prochaine version.",
            rate * 100,
            DIVERGENCE_ATTENTION_THRESHOLD * 100,
            comparable,
        )
    return rate
