from __future__ import annotations

import subprocess
import sys


def test_mlops_score_cli_runs_and_exits_zero_on_reference_agent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DB_URL", "sqlite:///:memory:")
    result = subprocess.run(
        [sys.executable, "-m", "velmo.mlops.score", "--min-score", "0.0"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),  # le CLI écrit "mlops/report.md" relatif au cwd
    )
    # `--min-score 0.0` : le gate ne peut jamais bloquer, isole ce test d'un
    # calibrage réel des seuils (hors périmètre CLI) — on vérifie seulement
    # que le point d'entrée s'exécute et produit un rapport.
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "mlops" / "report.md").exists()
