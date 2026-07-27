from __future__ import annotations

import json
from pathlib import Path

EVAL_PATH = Path(__file__).resolve().parent.parent / "eval" / "guardrail_cases.jsonl"


def _load() -> list[dict]:
    return [
        json.loads(line)
        for line in EVAL_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_guardrail_cases_has_enough_injection_cases_for_prompt_shields_calibration() -> None:
    cases = _load()
    injection = [c for c in cases if c["category"] == "prompt_injection"]
    assert len(injection) >= 8  # 4 aujourd'hui — seuil doublé pour un delta mesurable


def test_guardrail_cases_has_enough_output_pii_cases_for_pii_redaction_calibration() -> None:
    cases = _load()
    pii_output = [c for c in cases if c["category"] == "pii" and c["where"] == "output"]
    assert len(pii_output) >= 6  # 3 aujourd'hui (dont 1 texte libre) — seuil doublé


def test_g8_has_cases_at_both_control_points() -> None:
    """G8 s'applique à deux points que le tableau des garde-fous ignorait — le
    contenu récupéré et l'écriture mémoire. Sans cas pour chacun, les contrôles
    correspondants ne sont jamais exercés (audit Z-02/Z-03)."""
    cases = _load()
    for where in ("retrieved", "memory_write"):
        at_point = [c for c in cases if c["where"] == where]
        assert len(at_point) >= 2, f"aucun cas G8 en {where}"
        assert any(c["expected_action"] == "block" for c in at_point)
        # Un cas légitime par point : sans lui on ne mesure que le rappel, jamais
        # le taux de faux positifs — or écarter un extrait de FAQ valide dégrade
        # silencieusement chaque réponse qui en dépend.
        assert any(c["category"] == "legitimate" for c in at_point)
