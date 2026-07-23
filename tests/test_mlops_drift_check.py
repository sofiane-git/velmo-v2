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


def test_run_drift_check_persists_drift_rows(tmp_path) -> None:
    # D8-03 : chaque run de drift persiste ses mesures (règle « deux nuits
    # consécutives » de la conception §Rollback — impossible sans historique).
    from velmo.mlops.db import DriftCheckRun

    db_url = f"sqlite:///{tmp_path}/drift_persist.db"
    run_drift_check(["guardrails"], db_url=db_url)

    engine = make_mlops_engine(db_url)
    session = sessionmaker(bind=engine, future=True)()
    rows = session.query(DriftCheckRun).all()
    assert len(rows) == 1
    assert rows[0].suite == "guardrails"
    assert rows[0].triggered_by == "model-drift"
    assert 0.0 <= rows[0].note <= 1.0
    session.close()


def test_drift_floor_failures_flags_suites_below_min_score() -> None:
    # D8-03 : une note sous le plancher doit être signalée (le CLI sortira en
    # exit 1) ; au-dessus, rien. Fonction pure, pas d'appel LLM.
    from velmo.mlops.drift_check import DriftCheckResult, drift_floor_failures

    results = [
        DriftCheckResult("memory", 10, 9, 0.90),
        DriftCheckResult("guardrails", 10, 5, 0.50),
    ]
    failures = drift_floor_failures(results, min_score=0.80)
    assert failures == ["guardrails"]
    assert drift_floor_failures(results, min_score=0.40) == []
