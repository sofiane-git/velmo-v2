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
