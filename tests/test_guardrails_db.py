from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import sessionmaker

from velmo.guardrails.db import (
    count_recent_audit,
    list_recent_audit,
    make_guardrails_engine,
    write_audit,
)


def _session():
    engine = make_guardrails_engine("sqlite:///:memory:")
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def test_no_arg_resolves_configured_sqlite_db_url_not_hardcoded_default(monkeypatch, tmp_path):
    configured_path = tmp_path / "configured_guardrails.db"
    monkeypatch.setenv("DB_URL", f"sqlite:///{configured_path}")
    engine = make_guardrails_engine()
    assert engine.url.database == str(configured_path)


def test_write_and_list_audit():
    session = _session()
    write_audit(session, "u1", "prompt_injection", "input", "regex", None, "block", None)
    session.commit()
    rows = list_recent_audit(session, "u1")
    assert len(rows) == 1
    assert rows[0].category == "prompt_injection"
    assert rows[0].action == "block"


def test_count_recent_audit_only_counts_matching_category_and_action():
    session = _session()
    write_audit(session, "u2", "prompt_injection", "input", "regex", None, "block", None)
    write_audit(session, "u2", "prompt_injection", "input", "regex", None, "block", None)
    write_audit(session, "u2", "hate", "input", "classifier", 0.9, "block", None)
    session.commit()
    count = count_recent_audit(session, "u2", "prompt_injection", timedelta(hours=24))
    assert count == 2


def test_isolation_between_users():
    session = _session()
    write_audit(session, "u3", "hate", "input", "classifier", 0.9, "block", None)
    write_audit(session, "u4", "hate", "input", "classifier", 0.9, "block", None)
    session.commit()
    assert len(list_recent_audit(session, "u3")) == 1
    assert len(list_recent_audit(session, "u4")) == 1


def test_write_audit_accepts_missing_user_id():
    session = _session()
    write_audit(session, None, "hate", "input", "classifier", 0.9, "flag", None)
    session.commit()  # ne doit pas lever malgré user_id=None


def test_count_recent_audit_counts_block_escalate_too():
    session = _session()
    write_audit(session, "u5", "secret_leak", "input", "llm_judge", 0.95, "block_escalate", None)
    write_audit(session, "u5", "secret_leak", "input", "llm_judge", 0.72, "block", None)
    session.commit()
    count = count_recent_audit(session, "u5", "secret_leak", timedelta(hours=24))
    assert count == 2
