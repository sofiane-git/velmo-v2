"""Test fumée pour eval/calibrate_thresholds.py — s'exécute sans crash sur le
dataset (repli hors-ligne en CI, pas de credentials réseau), ne fait pas
d'assertion sur des valeurs de seuil précises (recalibrables)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"


def _load_calibrate_module():
    spec = importlib.util.spec_from_file_location(
        "calibrate_thresholds", EVAL_DIR / "calibrate_thresholds.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_calibrate_runs_without_crashing(capsys, monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

    module = _load_calibrate_module()
    module.calibrate()

    output = capsys.readouterr().out
    assert "Seuils candidats" in output
