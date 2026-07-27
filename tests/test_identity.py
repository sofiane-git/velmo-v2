"""Résolution d'identité de l'API — contrat Ch.0 §2 (audit Z-05).

R3 (isolation) garantit qu'aucune requête n'atteint les données d'un autre
utilisateur *pour un `user_id` donné*. Elle ne dit rien de l'authenticité de ce
`user_id` : l'API l'acceptait dans le corps de requête, sur un service non
authentifié, alors que la conception affirmait « jamais du contenu du message ».

Contrat testé ici : l'identité vient d'un en-tête de confiance posé par la
couche d'authentification ; accepter le corps de requête est un repli de
développement **opt-in**, refusé au démarrage en production.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import seeded_session

from velmo.agent import Agent
from velmo.api import app, get_agent, resolve_user_id
from velmo.config import ConfigurationError, Settings, validate_startup
from velmo.guardrails import GuardrailEngine
from velmo.kb_store import LocalKB
from velmo.llm import EchoLLM
from velmo.memory import MemoryManager

TRUSTED_HEADER = "X-Velmo-User"


def _hermetic_agent() -> Agent:
    return Agent(
        llm=EchoLLM(),
        memory=MemoryManager(db_url="sqlite:///:memory:"),
        guardrails=GuardrailEngine(db_url="sqlite:///:memory:"),
        session=seeded_session(),
        kb=LocalKB(),
    )


# --------------------------------------------------------------- fail-fast prod


def test_production_without_trusted_header_refuses_to_start():
    """Le cas qui motive tout le reste : en production, un endpoint dont
    l'identité est déclarative est usurpable. Un repli silencieux y est pire
    qu'une panne — il donne l'apparence de l'isolation."""
    settings = Settings(environment="production")
    with pytest.raises(ConfigurationError, match="TRUSTED_USER_HEADER"):
        validate_startup(settings)


def test_production_with_trusted_header_starts():
    settings = Settings(environment="production", trusted_user_header=TRUSTED_HEADER)
    validate_startup(settings)


def test_production_can_opt_in_to_unauthenticated_explicitly():
    """Le repli reste possible en production, mais seulement déclaré — jamais
    par défaut (même patron que `allow_sqlite_fallback`)."""
    settings = Settings(environment="production", allow_unauthenticated_user_id=True)
    validate_startup(settings)


def test_development_without_trusted_header_starts():
    validate_startup(Settings(environment="development"))


# ------------------------------------------------------- résolution d'identité


def test_trusted_header_wins_over_request_body():
    """Le point central : si les deux sont présents, l'en-tête de confiance
    gagne. Sinon un appelant contournerait l'authentification en postant un
    autre `user_id` dans le JSON métier."""
    settings = Settings(trusted_user_header=TRUSTED_HEADER)
    assert (
        resolve_user_id(header_value="C-authentifie", body_value="C-usurpe", settings=settings)
        == "C-authentifie"
    )


def test_body_ignored_when_trusted_header_configured_but_absent():
    """En-tête configuré mais absent de la requête = requête non authentifiée.
    On ne retombe pas sur le corps : ce serait rendre l'en-tête décoratif."""
    settings = Settings(trusted_user_header=TRUSTED_HEADER)
    with pytest.raises(PermissionError):
        resolve_user_id(header_value=None, body_value="C-marc-dubois", settings=settings)


def test_body_accepted_when_no_trusted_header_configured():
    """Mode démonstrateur (Ch.0 §1) : aucune couche d'identité, le corps est
    toléré."""
    assert (
        resolve_user_id(header_value=None, body_value="C-marc-dubois", settings=Settings())
        == "C-marc-dubois"
    )


def test_missing_identity_entirely_is_refused():
    with pytest.raises(PermissionError):
        resolve_user_id(header_value=None, body_value=None, settings=Settings())


def test_empty_header_treated_as_absent():
    settings = Settings(trusted_user_header=TRUSTED_HEADER)
    with pytest.raises(PermissionError):
        resolve_user_id(header_value="   ", body_value="C-marc-dubois", settings=settings)


# ----------------------------------------------------------- bout en bout API


def test_chat_uses_trusted_header_not_body(monkeypatch):
    """Vérification de bout en bout : le tour est traité pour l'identité de
    l'en-tête, pas pour celle du corps."""
    monkeypatch.setenv("TRUSTED_USER_HEADER", TRUSTED_HEADER)
    seen: list[str] = []

    agent = _hermetic_agent()
    # `traced_reply` passe par `respond_traced` (générateur d'étapes), pas par
    # `reply` : c'est là qu'on observe le `user_id` réellement utilisé.
    original = agent.respond_traced

    def _recording(user_id: str, message: str):
        seen.append(user_id)
        return original(user_id, message)

    agent.respond_traced = _recording  # type: ignore[method-assign]
    app.dependency_overrides[get_agent] = lambda: agent
    client = TestClient(app)
    try:
        response = client.post(
            "/chat",
            json={"user_id": "C-usurpe", "message": "Bonjour"},
            headers={TRUSTED_HEADER: "C-marc-dubois"},
        )
        assert response.status_code == 200
        assert seen == ["C-marc-dubois"]
    finally:
        app.dependency_overrides.clear()


def test_chat_refuses_when_trusted_header_configured_but_missing(monkeypatch):
    monkeypatch.setenv("TRUSTED_USER_HEADER", TRUSTED_HEADER)
    app.dependency_overrides[get_agent] = _hermetic_agent
    client = TestClient(app)
    try:
        response = client.post("/chat", json={"user_id": "C-marc-dubois", "message": "Bonjour"})
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()
