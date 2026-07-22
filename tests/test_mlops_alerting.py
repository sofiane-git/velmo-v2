"""Alerte deux-nuits-consécutives (audit D8-04, conception ch.3 §Rollback :
« si une dimension gate passe sous son seuil deux nuits consécutives, une
alerte est levée → décision humaine de rollback »)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import sessionmaker

from velmo.mlops.alerting import consecutive_breaches
from velmo.mlops.db import EvalRun, make_mlops_engine, utcnow


def _seed_run(session, *, note_memory=1.0, note_guardrails=1.0, note_quality=1.0, days_ago=0):
    run = EvalRun(
        id=f"run-{days_ago}-{note_memory}-{note_guardrails}-{note_quality}",
        version_tag="v-test",
        note_memory=note_memory,
        note_guardrails=note_guardrails,
        note_quality=note_quality,
        note_globale=min(note_memory, note_guardrails, note_quality),
        global_gate=min(note_memory, note_guardrails, note_quality),
        gate_passed=True,
        block_rate=0.0,
        false_positive_rate=0.0,
        latency_p50_ms=10.0,
        latency_p95_ms=20.0,
        cost_per_conv=0.001,
        ran_at=utcnow() - timedelta(days=days_ago),
        triggered_by="nightly",
    )
    session.add(run)


def _session(tmp_path, name):
    from velmo.mlops.db import AgentVersion

    engine = make_mlops_engine(f"sqlite:///{tmp_path}/{name}.db")
    session = sessionmaker(bind=engine, future=True)()
    session.add(
        AgentVersion(
            version_tag="v-test",
            prompt_hash="a" * 64,
            memory_config_hash="b" * 64,
            guardrail_config_hash="c" * 64,
            git_commit="deadbeef",
        )
    )
    session.commit()
    return session


def test_two_consecutive_breaches_flag_dimension(tmp_path) -> None:
    session = _session(tmp_path, "two_breaches")
    _seed_run(session, note_guardrails=0.5, days_ago=1)
    _seed_run(session, note_guardrails=0.6, days_ago=0)
    session.commit()

    breaches = consecutive_breaches(session, min_score=0.80)
    assert breaches == ["guardrails"]
    session.close()


def test_single_breach_does_not_alert(tmp_path) -> None:
    # Filtre anti-bruit : 1 seule nuit sous le plancher = pas d'alerte
    # (« sans bloquer pour du bruit », reco expert).
    session = _session(tmp_path, "one_breach")
    _seed_run(session, note_memory=1.0, days_ago=1)
    _seed_run(session, note_memory=0.5, days_ago=0)
    session.commit()

    assert consecutive_breaches(session, min_score=0.80) == []
    session.close()


def test_no_runs_no_alert(tmp_path) -> None:
    session = _session(tmp_path, "empty")
    assert consecutive_breaches(session, min_score=0.80) == []
    session.close()
