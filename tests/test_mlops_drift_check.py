from __future__ import annotations

from velmo.mlops.db import EvalRun, make_mlops_engine
from velmo.mlops.drift_check import run_drift_check
from sqlalchemy.orm import sessionmaker


def test_run_drift_check_runs_only_requested_suite(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/drift_guardrails.db"
    results = run_drift_check(["guardrails"], db_url=db_url)

    assert [r.suite for r in results] == ["guardrails"]
    assert results[0].cases > 0
    assert 0.0 <= results[0].note <= 1.0


def test_run_drift_check_supports_multiple_suites(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/drift_multi.db"
    results = run_drift_check(["memory", "quality"], db_url=db_url)

    assert [r.suite for r in results] == ["memory", "quality"]
    for result in results:
        assert result.cases > 0


def test_run_drift_check_does_not_persist_eval_run(tmp_path) -> None:
    # Contrainte centrale : un run partiel ne doit jamais toucher la table du
    # gate (`EvalRun`), dont `note_memory`/`note_guardrails`/`note_quality`
    # sont NOT NULL et supposent les 3 suites présentes.
    db_url = f"sqlite:///{tmp_path}/drift_no_gate.db"
    run_drift_check(["guardrails"], db_url=db_url)

    engine = make_mlops_engine(db_url)
    session = sessionmaker(bind=engine, future=True)()
    assert session.query(EvalRun).count() == 0
    session.close()
