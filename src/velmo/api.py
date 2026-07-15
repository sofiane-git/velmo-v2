from __future__ import annotations

import json
import os
from collections.abc import Iterator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .agent import Agent, build_default_agent

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
_default_web_origins = "http://localhost:3000,http://127.0.0.1:3000"
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("VELMO_WEB_ORIGINS", _default_web_origins).split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# On instancie l'agent par défaut au démarrage
agent = build_default_agent()


def get_agent() -> Agent:
    return agent


class ChatRequest(BaseModel):
    user_id: str
    message: str


class ChatResponse(BaseModel):
    response: str


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest, agent: Agent = Depends(get_agent)) -> ChatResponse:
    """
    Envoie un message à l'agent Velmo pour un utilisateur donné.
    """
    try:
        response_text = agent.respond(request.user_id, request.message)
        return ChatResponse(response=response_text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _sse_format(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_events(agent: Agent, user_id: str, message: str) -> Iterator[str]:
    for event_type, payload in agent.respond_traced(user_id, message):
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


@app.get("/health")
def health_check() -> dict[str, str]:
    """
    Vérifie que l'API est fonctionnelle.
    """
    return {"status": "ok"}
