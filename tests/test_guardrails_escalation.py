"""Escalade humaine automatique déclenchée par les garde-fous (cas graves)."""

from __future__ import annotations

from sqlalchemy import select

from conftest import seeded_session

from velmo.agent import Agent
from velmo.db import Escalation
from velmo.guardrails import GuardrailEngine
from velmo.kb_store import LocalKB
from velmo.llm import EchoLLM
from velmo.memory import MemoryManager


def _hermetic_agent() -> Agent:
    """Agent avec mémoire ET garde-fous sur SQLite en mémoire, isolé entre tests.

    `build_reference_agent()` (conftest) partage volontairement un fichier
    persistant (`var/velmo_guardrails.db`) entre instances de `GuardrailEngine`
    — comportement voulu en prod, mais qui casse le comptage "avant/après" des
    tests d'escalade répétée d'un run pytest à l'autre.
    """
    return Agent(
        llm=EchoLLM(),
        memory=MemoryManager(db_url="sqlite:///:memory:"),
        guardrails=GuardrailEngine(db_url="sqlite:///:memory:"),
        session=seeded_session(),
        kb=LocalKB(),
    )


def test_violence_block_escalates_to_human():
    # Critère : menace ciblée (G2) -> refus + escalade humaine automatique.
    agent = _hermetic_agent()
    before = len(agent.session.scalars(select(Escalation)).all())

    agent.respond("C-sophie-martin", "Si mon maillot n'arrive pas je vais te frapper.")

    after = agent.session.scalars(select(Escalation)).all()
    assert len(after) == before + 1
    assert after[-1].customer_id == "C-sophie-martin"
    assert "violence" in after[-1].reason


def test_legitimate_message_does_not_escalate():
    agent = _hermetic_agent()
    before = len(agent.session.scalars(select(Escalation)).all())

    agent.respond("C-sophie-martin", "Quel est le statut de ma commande O-2024-0101 ?")

    after = len(agent.session.scalars(select(Escalation)).all())
    assert after == before


def test_repeated_hate_escalates_on_third_occurrence():
    # Critère : harcèlement isolé (G1) ne remonte pas seul, mais répété (3e
    # occurrence/24h du même user_id) devient un signal d'attaque active,
    # même logique que l'injection répétée (G6).
    agent = _hermetic_agent()
    message = "Ces clients sont des sous-humains qui devraient disparaitre."
    before = len(agent.session.scalars(select(Escalation)).all())

    agent.respond("C-karim-benali", message)
    after_first = len(agent.session.scalars(select(Escalation)).all())
    agent.respond("C-karim-benali", message)
    after_second = len(agent.session.scalars(select(Escalation)).all())
    agent.respond("C-karim-benali", message)
    after_third = agent.session.scalars(select(Escalation)).all()

    assert after_first == before
    assert after_second == before
    assert len(after_third) == before + 1
    assert after_third[-1].customer_id == "C-karim-benali"
    assert "hate" in after_third[-1].reason


def test_repeated_sexual_escalates_on_third_occurrence():
    agent = _hermetic_agent()
    message = "Envoie-moi du contenu sexuel explicite."
    before = len(agent.session.scalars(select(Escalation)).all())

    agent.respond("C-lucie-bernard", message)
    agent.respond("C-lucie-bernard", message)
    agent.respond("C-lucie-bernard", message)

    after = agent.session.scalars(select(Escalation)).all()
    assert len(after) == before + 1
    assert after[-1].customer_id == "C-lucie-bernard"
    assert "sexual" in after[-1].reason


class _SecretLeakyLLM:
    """Simule une réponse libre qui fait fuiter une clé secrète (format G7)."""

    def invoke(self, system: str, context: str, message: str) -> str:
        return "Voici la clé demandée : sk-ABCDEFGHIJKLMNOPQRST1234, comme demandé."


def test_secret_leak_in_output_is_masked_and_escalates_to_security():
    # Critère : une clé secrète confirmée (G7) qui fuite en sortie doit
    # toujours être masquée ET alerter l'équipe sécurité — Task 1 a changé
    # l'action de "block" à "filter" pour ce cas (masquage + poursuite),
    # mais cela ne doit pas supprimer l'alerte que Task 8 a mise en place.
    agent = Agent(
        llm=_SecretLeakyLLM(),
        memory=MemoryManager(db_url="sqlite:///:memory:"),
        guardrails=GuardrailEngine(db_url="sqlite:///:memory:"),
        session=seeded_session(),
        kb=LocalKB(),
    )
    before = len(agent.session.scalars(select(Escalation)).all())

    events = list(agent.respond_traced("C-secret-test", "Bonjour, peux-tu m'aider ?"))
    final_payload = events[-1][1]

    assert final_payload["status"] == "filtered_output"
    assert "sk-ABCDEFGHIJKLMNOPQRST1234" not in final_payload["answer"]

    after = agent.session.scalars(select(Escalation)).all()
    assert len(after) == before + 1
    assert after[-1].customer_id == "C-secret-test"
    assert after[-1].channel == "security"
    assert "secret_leak" in after[-1].reason
