"""Orchestration du tour via LangGraph : un `StateGraph` à un nœud
(`append_turn`), persisté par thread_id via un checkpointer Postgres (prod) ou
SQLite (hors-ligne) — remplace les tables maison `Conversation`/`Message`
supprimées en LangChain 1.x (`docs/job/conceptions/conception_chantier1_memoire.md`).

`get_checkpointer` suit la même convention que `memory.db.make_memory_engine` :
Postgres réel si joignable, sinon repli SQLite fichier persistant (jamais
`:memory:` par défaut, pour que deux instances séparées partagent l'état — R2).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Annotated, Iterator, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)


def _extend_messages(
    existing: list[dict[str, str]], new: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Reducer : chaque `graph.invoke(...)` doit accumuler les messages du tour,
    pas écraser le fil (comportement par défaut de LangGraph sans reducer
    explicite) — sinon `write()` perdrait l'historique à chaque nouveau tour."""
    return existing + new


class TurnState(TypedDict):
    """État du graphe : le fil de messages du thread (source de vérité, R1)."""

    messages: Annotated[list[dict[str, str]], _extend_messages]


def _append_turn(state: TurnState) -> dict[str, list[dict[str, str]]]:
    # Nœud unique : le tour est déjà construit par l'appelant (`write()`), ce
    # nœud ne fait qu'acter la persistance par le checkpointer — le reducer
    # `_extend_messages` (ci-dessus) fait l'accumulation, pas ce nœud.
    return {"messages": state["messages"]}


def build_graph(checkpointer: BaseCheckpointSaver[str]) -> CompiledStateGraph[TurnState]:
    builder: StateGraph[TurnState] = StateGraph(TurnState)
    builder.add_node("append_turn", _append_turn)
    builder.set_entry_point("append_turn")
    return builder.compile(checkpointer=checkpointer)


@contextmanager
def get_checkpointer(db_url: str) -> Iterator[BaseCheckpointSaver[str]]:
    """Résout un checkpointer Postgres si `db_url` est joignable, sinon SQLite
    fichier. Importe `_postgres_reachable`/`_default_sqlite_path` de
    `memory.db` pour rester sur une seule logique de résolution d'engine."""
    from velmo.memory.db import _default_sqlite_path, _postgres_reachable

    if db_url.startswith("postgresql") and _postgres_reachable(db_url):
        with PostgresSaver.from_conn_string(db_url) as checkpointer:
            checkpointer.setup()
            yield checkpointer
        return

    if db_url.startswith("postgresql"):
        logger.warning(
            "Postgres injoignable (%r) pour les checkpoints LangGraph : repli SQLite.", db_url
        )
        path = str(_default_sqlite_path().with_name("velmo_checkpoints.db"))
    elif db_url.startswith("sqlite:///") and ":memory:" not in db_url:
        path = db_url.removeprefix("sqlite:///")
    else:
        path = ":memory:"

    with SqliteSaver.from_conn_string(path) as checkpointer:
        yield checkpointer
