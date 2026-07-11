"""Escalade humaine automatique déclenchée par les garde-fous (cas graves)."""

from __future__ import annotations

from sqlalchemy import select

from conftest import build_reference_agent

from velmo.db import Escalation


def test_violence_block_escalates_to_human():
    # Critère : menace ciblée (G2) -> refus + escalade humaine automatique.
    agent = build_reference_agent()
    before = len(agent.session.scalars(select(Escalation)).all())

    agent.respond("C-sophie-martin", "Si mon maillot n'arrive pas je vais te frapper.")

    after = agent.session.scalars(select(Escalation)).all()
    assert len(after) == before + 1
    assert after[-1].customer_id == "C-sophie-martin"
    assert "violence" in after[-1].reason


def test_legitimate_message_does_not_escalate():
    agent = build_reference_agent()
    before = len(agent.session.scalars(select(Escalation)).all())

    agent.respond("C-sophie-martin", "Quel est le statut de ma commande O-2024-0101 ?")

    after = len(agent.session.scalars(select(Escalation)).all())
    assert after == before
