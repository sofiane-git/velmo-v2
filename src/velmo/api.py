from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from velmo.config import get_settings, validate_startup
from velmo.guardrails import GuardrailEngine
from velmo.guardrails.classifier import get_classifier
from velmo.guardrails.judge import get_judge
from velmo.llm import get_llm
from velmo.memory import MemoryManager
from velmo.memory.extractor import get_extractor
from velmo.mlops.observability import (
    InstrumentedClassifier,
    InstrumentedExtractor,
    InstrumentedJudge,
    InstrumentedLLM,
    traced_reply,
    traced_respond,
)

from .agent import Agent, build_default_agent
from .db import Customer
from .tools._common import select

# Échoue tôt si une intégration Azure est à moitié configurée (endpoint sans
# clé ou l'inverse) — avant que le process ne serve du trafic, pas à la
# première requête qui la découvre.
_settings = get_settings()
validate_startup(_settings)

app = FastAPI(
    title="Velmo 2.0 API",
    description="API pour l'agent de support Velmo 2.0 (boutique de maillots de foot collector).",
    version="2.0.0",
)

# Outil de démo interne sans authentification (cf. spec) : le frontend Nuxt
# tourne sur une origine distincte (port 3000 par défaut) et appelle
# /chat/stream en fetch cross-origin — sans CORS le navigateur bloque la
# requête SSE. Allowlist explicite (pas de wildcard) même si le service reste
# non authentifié.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.velmo_web_origins.split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def _build_traced_agent() -> Agent:
    """Assemble l'agent servi par l'API avec chaque composant LLM enveloppé
    en résolution dynamique (`sink=None` — Task 3) : `MemoryManager` et
    `GuardrailEngine` restent des singletons process (une connexion DB/
    checkpointer chacun), mais `traced_respond` (Task 4) pose un sink
    *différent par tour de conversation* dans le contexte au moment de
    l'appel — même principe que `mlops.cli._build_instrumented_agent`, sans
    le sink fixe (un run CI = un sink ; ici un process = N tours)."""
    raw_llm = get_llm()
    llm = InstrumentedLLM(raw_llm, None, "agent", _settings.azure_ai_inference_model)
    memory = MemoryManager(
        extractor=InstrumentedExtractor(
            get_extractor(), None, "memory_extractor", _settings.anthropic_async_model
        ),
        llm=InstrumentedLLM(raw_llm, None, "memory_summary", _settings.azure_ai_inference_model),
    )
    guardrails = GuardrailEngine(
        classifier=InstrumentedClassifier(get_classifier(), None, "guardrails_classifier"),
        judge=InstrumentedJudge(
            get_judge(), None, "guardrails_judge", _settings.azure_openai_guard_deployment
        ),
    )
    return build_default_agent(llm=llm, memory=memory, guardrails=guardrails)


# On instancie l'agent par défaut au démarrage
agent = _build_traced_agent()


def get_agent() -> Agent:
    return agent


class ChatRequest(BaseModel):
    user_id: str
    message: str


class ChatResponse(BaseModel):
    response: str


class CustomerOut(BaseModel):
    id: str
    full_name: str


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest, agent: Agent = Depends(get_agent)) -> ChatResponse:
    """
    Envoie un message à l'agent Velmo pour un utilisateur donné.
    """
    try:
        response_text = traced_reply(agent, request.user_id, request.message)
        return ChatResponse(response=response_text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _sse_format(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_events(agent: Agent, user_id: str, message: str) -> Iterator[str]:
    for event_type, payload in traced_respond(agent, user_id, message):
        yield _sse_format(event_type, payload)


@app.post("/chat/stream")
def chat_stream_endpoint(
    request: ChatRequest, agent: Agent = Depends(get_agent)
) -> StreamingResponse:
    """
    Même contrat que `/chat`, mais diffuse chaque étape du pipeline en SSE :
    `input_guardrail`, `memory_read`, `routing`, `tool_result`? (si un outil a
    été appelé), `output_guardrail`, `memory_write`, `final`.
    """
    return StreamingResponse(
        _stream_events(agent, request.user_id, request.message),
        media_type="text/event-stream",
    )


@app.post("/memory/{user_id}/clear-session")
def clear_session_endpoint(user_id: str, agent: Agent = Depends(get_agent)) -> dict[str, bool]:
    """
    Termine la conversation en cours d'un utilisateur (équivalent `/clear`) :
    l'historique et le résumé du thread actif sont abandonnés, la mémoire
    long terme (faits, procédures, épisodes) n'est pas affectée.
    """
    agent.memory.clear_session(user_id)
    return {"cleared": True}


@app.get("/customers", response_model=list[CustomerOut])
def list_customers(agent: Agent = Depends(get_agent)) -> list[CustomerOut]:
    """
    Liste les clients inscrits, pour peupler le sélecteur d'utilisateur du
    frontend de démo.
    """
    rows = agent.session.execute(
        select(Customer.id, Customer.full_name).order_by(Customer.full_name)
    ).all()
    return [CustomerOut(id=i, full_name=n) for i, n in rows]


@app.get("/health")
def health_check() -> dict[str, str]:
    """
    Vérifie que l'API est fonctionnelle.
    """
    return {"status": "ok"}
