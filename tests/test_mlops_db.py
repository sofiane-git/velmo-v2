from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from velmo.mlops.db import AgentVersion, EvalCaseResult, EvalRun, make_mlops_engine


def test_agent_version_eval_run_case_result_roundtrip() -> None:
    engine = make_mlops_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine, future=True)
    session = Session()

    version = AgentVersion(
        version_tag="v0.0.0-test",
        prompt_hash="a" * 64,
        memory_config_hash="b" * 64,
        guardrail_config_hash="c" * 64,
        git_commit="abc1234",
    )
    session.add(version)
    session.commit()

    run = EvalRun(
        id="run-1",
        version_tag="v0.0.0-test",
        note_memory=1.0,
        note_guardrails=0.95,
        note_quality=0.9,
        note_globale=0.96,
        global_gate=0.9,
        gate_passed=True,
        block_rate=0.95,
        false_positive_rate=0.05,
        latency_p50_ms=100.0,
        latency_p95_ms=300.0,
        cost_per_conv=0.01,
        triggered_by="ci",
    )
    session.add(run)
    session.commit()

    case = EvalCaseResult(
        id="case-1", run_id="run-1", case_id="R1-marc-3commandes", suite="memory",
        passed=True, score=1.0, latency_ms=50.0, retried=False, error_kind=None,
    )
    session.add(case)
    session.commit()

    reloaded = session.get(EvalRun, "run-1")
    assert reloaded is not None
    assert reloaded.version_tag == "v0.0.0-test"
