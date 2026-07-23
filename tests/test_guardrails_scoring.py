from __future__ import annotations

from velmo.guardrails._scoring import FALLBACK_MAX_SCORE


def test_fallback_max_score_clears_block_but_not_escalate():
    # Doit rester au-dessus de BLOCK_THRESHOLD (0.7) pour que les replis
    # bloquent toujours, mais strictement en dessous de ESCALATE_THRESHOLD
    # (0.9) pour qu'un simple mot-clé matché ne déclenche jamais
    # l'auto-escalade à lui seul (cf. pipeline.py, Task 4).
    assert 0.7 <= FALLBACK_MAX_SCORE < 0.9
