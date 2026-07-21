from __future__ import annotations

import json

from velmo.mlops import Scores
from velmo.mlops.report import write_report


def _sample_scores() -> Scores:
    return Scores(
        memory=0.98, guardrails=0.91, quality=0.85, global_=0.85,
        block_rate=0.95, false_positive_rate=0.05, latency_ms=320.0, cost=0.012,
    )


def test_write_report_creates_markdown_with_required_signals(tmp_path) -> None:
    path = tmp_path / "report.md"
    write_report(_sample_scores(), path)
    text = path.read_text(encoding="utf-8").lower()
    for signal in ["memoire", "blocage", "faux positif", "latence", "cout"]:
        assert signal in text


def test_write_report_creates_json_sidecar(tmp_path) -> None:
    path = tmp_path / "report.md"
    write_report(_sample_scores(), path)
    sidecar = path.with_suffix(".json")
    assert sidecar.exists()
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["note_memoire"] == 0.98
    assert data["note_qualite"] == 0.85
