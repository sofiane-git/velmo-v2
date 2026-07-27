"""Fixtures de test : base SQLite seedée, FAQ locale, agents — tout hors-ligne."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from velmo.agent import Agent
from velmo.db import fresh_sqlite_session
from velmo.guardrails import Decision, GuardrailEngine
from velmo.kb_store import LocalKB
from velmo.llm import EchoLLM
from velmo.memory import MemoryManager
from velmo.sampledata import seed

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"


def load_jsonl(name: str) -> list[dict]:
    text = (EVAL_DIR / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def seeded_session():
    session = fresh_sqlite_session()
    seed(session)
    return session


class AllowAllGuardrails:
    """Garde-fous neutralisés (agent dégradé pour le test de régression)."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def check_input(
        self, message: str, user_id: str | None = None, source_thread_id: str | None = None
    ) -> Decision:
        return Decision(allowed=True, action="allow")

    def check_output(
        self, text: str, user_id: str | None = None, source_thread_id: str | None = None
    ) -> Decision:
        return Decision(allowed=True, action="allow")


def build_reference_agent() -> Agent:
    # `db_url` explicite (SQLite en mémoire, isolé par instance) : sans lui,
    # MemoryManager/GuardrailEngine retombent sur `Settings.db_url` — un
    # Postgres ambiant (démarré à côté, ex. `make up`) ou un fichier SQLite
    # partagé (`var/velmo_*.db`) entre tests, au lieu d'un état isolé.
    return Agent(
        llm=EchoLLM(),
        memory=MemoryManager(db_url="sqlite:///:memory:"),
        guardrails=GuardrailEngine(db_url="sqlite:///:memory:"),
        session=seeded_session(),
        kb=LocalKB(),
    )


def build_degraded_agent() -> Agent:
    return Agent(
        llm=EchoLLM(),
        memory=MemoryManager(db_url="sqlite:///:memory:"),
        guardrails=AllowAllGuardrails(),
        session=seeded_session(),
        kb=LocalKB(),
    )


@pytest.fixture
def db_session():
    session = seeded_session()
    yield session
    session.close()


@pytest.fixture
def kb() -> LocalKB:
    return LocalKB()


@pytest.fixture
def reference_agent() -> Agent:
    return build_reference_agent()


@pytest.fixture
def degraded_agent() -> Agent:
    return build_degraded_agent()


def intent_token(session, *, user_id: str, tool: str, arguments: dict) -> str:
    """Jeton de confirmation valide pour une action de classe I (Ch.4 §A4).

    Les outils irréversibles exigent une confirmation préparée à un tour
    antérieur ; les tests qui vérifient une *autre* propriété (plafond,
    idempotence, journal) passent par ce helper plutôt que de dupliquer la
    préparation."""
    from velmo.tools._intent import prepare_intent

    intent = prepare_intent(
        session,
        user_id=user_id,
        tool=tool,
        arguments=arguments,
        resource_id=arguments.get("order_id"),
    )
    assert intent.token is not None, f"préparation refusée : {intent.refusal}"
    return intent.token


def refund_token(session, order_id: str, user_id: str, amount: float, reason: str) -> str:
    return intent_token(
        session,
        user_id=user_id,
        tool="trigger_refund",
        arguments={"order_id": order_id, "amount": amount, "reason": reason},
    )


def cancel_token(session, order_id: str, user_id: str) -> str:
    return intent_token(
        session, user_id=user_id, tool="cancel_order", arguments={"order_id": order_id}
    )
