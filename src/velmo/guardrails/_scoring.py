"""Constante de score partagée par les signaux sans confiance calibrée
(correspondances lexicales déterministes des replis hors-ligne). Reste
au-dessus de `pipeline.BLOCK_THRESHOLD` (le blocage se déclenche toujours)
mais strictement en dessous de `pipeline.ESCALATE_THRESHOLD` (pas
d'auto-escalade sur un signal qui n'a jamais mesuré sa propre confiance —
pour ces cas, l'escalade reste uniquement pilotée par la catégorie ou la
répétition, cf. `guardrails/__init__.py`).
"""

from __future__ import annotations

FALLBACK_MAX_SCORE = 0.75
