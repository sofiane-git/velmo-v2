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
import re
from contextlib import contextmanager
from typing import Annotated, Any, Iterator, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)


_REPLACE_KEY = "__replace__"

_SQLALCHEMY_DRIVER_SUFFIX_RE = re.compile(r"^postgresql\+[^:]+://")


def _to_psycopg_conninfo(url: str) -> str:
    """`db_url` circule partout au format SQLAlchemy (`postgresql+psycopg://...`,
    cf. `Settings.db_url`) — SQLAlchemy comprend ce `+driver`, mais
    `psycopg.Connection.connect()` (appelé directement par
    `PostgresSaver.from_conn_string`, sans passer par SQLAlchemy) attend une
    chaîne libpq/URI native et rejette le suffixe avec `ProgrammingError:
    missing "=" after ...`. On le retire avant l'appel psycopg uniquement —
    `create_engine`/`_postgres_reachable` (SQLAlchemy) restent inchangés."""
    return _SQLALCHEMY_DRIVER_SUFFIX_RE.sub("postgresql://", url)


def replace_messages(messages: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    """Enveloppe `messages` pour `graph.update_state(...)` : signale au reducer
    `_extend_messages` de remplacer le fil en place plutôt que de l'étendre.
    `update_state` applique le même reducer qu'un retour de nœud — sans ce
    marqueur, il concatènerait la version expurgée à la suite de l'originale
    au lieu de la remplacer (échec silencieux du scrub RGPD, cf.
    `MemoryManager._scrub_thread_messages`). Reste un `dict` JSON-sérialisable
    (contrainte du checkpointer, qui persiste la valeur brute avant reduction)."""
    return {_REPLACE_KEY: messages}


def _extend_messages(existing: list[dict[str, str]], new: Any) -> list[dict[str, str]]:
    """Reducer : chaque `graph.invoke(...)` doit accumuler les messages du tour,
    pas écraser le fil (comportement par défaut de LangGraph sans reducer
    explicite) — sinon `write()` perdrait l'historique à chaque nouveau tour.
    Exception : `new` enveloppé via `replace_messages` (cf. ci-dessus)."""
    if isinstance(new, dict) and _REPLACE_KEY in new:
        replaced: list[dict[str, str]] = new[_REPLACE_KEY]
        return replaced
    appended: list[dict[str, str]] = existing + new
    return appended


class TurnState(TypedDict):
    """État du graphe : le fil de messages du thread (source de vérité, R1)."""

    messages: Annotated[list[dict[str, str]], _extend_messages]


def _append_turn(state: TurnState) -> dict[str, list[dict[str, str]]]:
    # Nœud unique : le tour est déjà construit par l'appelant (`write()`), et
    # déjà fusionné dans `state["messages"]` par le reducer `_extend_messages`
    # au moment où `graph.invoke(...)` applique son état d'entrée — avant même
    # que ce nœud ne s'exécute. Ne rien retourner ici : renvoyer `state["messages"]`
    # le referait fusionner une deuxième fois (le reducer ne remplace pas, il
    # concatène), doublant le fil à chaque tour.
    return {}


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
        with PostgresSaver.from_conn_string(_to_psycopg_conninfo(db_url)) as checkpointer:
            checkpointer.setup()
            yield checkpointer
        return

    if db_url.startswith("postgresql"):
        from velmo.config import require_durable_store

        require_durable_store("checkpoints LangGraph", db_url)
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
