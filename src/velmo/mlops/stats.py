"""Non-régression statistique (M4) : la Suite Qualité est la seule dimension
intrinsèquement bruitée (jugement LLM) — un seuil absolu figé produirait des
faux blocages au moindre écart de calibration du juge. On bloque si
`moyenne_courante < baseline − 2σ`, jamais sur un seuil isolé (voir
conception_chantier3_evaluation_mlops.md §Éviter de bloquer pour du bruit).
"""

from __future__ import annotations

import statistics


def non_regression_ok(
    baseline_scores: list[float], current_scores: list[float], n_sigma: float = 2.0
) -> bool:
    """Vrai si la moyenne courante ne descend pas sous `moyenne_baseline − n_sigma·σ_baseline`.

    `σ_baseline` calculé sur l'échantillon baseline (N ≥ 2) ; si un seul score
    baseline est fourni (σ indéfini), on retombe sur une comparaison de
    moyennes strictes (pas de marge de bruit à absorber sans échantillon)."""
    baseline_mean = statistics.mean(baseline_scores)
    current_mean = statistics.mean(current_scores)
    if len(baseline_scores) < 2:
        return current_mean >= baseline_mean
    baseline_stdev = statistics.stdev(baseline_scores)
    return current_mean >= (baseline_mean - n_sigma * baseline_stdev)
