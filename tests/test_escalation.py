"""Deux canaux d'escalade distincts (support vs sécurité) — `escalate_to_human`."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from velmo.db import Escalation, make_engine
from velmo.tools.escalation import escalate_to_human


def _session() -> Session:
    engine = make_engine("sqlite:///:memory:")
    from velmo.db import Base

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_escalate_records_channel_support_by_default() -> None:
    session = _session()
    result = escalate_to_human(session, "cust-1", "menace concrète")
    assert result["channel"] == "support"
    row = session.get(Escalation, result["escalation_id"])
    assert row is not None
    assert row.channel == "support"


def test_escalate_records_channel_security_when_specified() -> None:
    session = _session()
    result = escalate_to_human(session, "cust-1", "fuite de secret confirmée", channel="security")
    assert result["channel"] == "security"
    row = session.get(Escalation, result["escalation_id"])
    assert row is not None
    assert row.channel == "security"
