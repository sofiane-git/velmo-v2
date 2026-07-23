"""Mémoire épisodique : backend pgvector (prod, embeddings réels dans la même
transaction que le texte — voir conception_chantier1_memoire.md §Store
épisodique) et backend local (hors-ligne, scoring mots-clés).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from velmo.config import get_settings


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _strip_accents(text.lower())) if len(t) > 2}


class EpisodicVectorStore(Protocol):
    """`add`/`delete` prennent l'`episode_id` déjà généré par `db.add_episode`
    (même id que la ligne SQL, plus de génération d'id indépendante côté store) —
    pour `PgVectorEpisodic` les deux sont des no-op (le texte + l'embedding vivent
    déjà dans cette ligne, même transaction) ; pour `LocalEpisodic` (repli
    hors-ligne, sans table), ce sont les seules écritures/suppressions réelles."""

    def add(self, user_id: str, summary: str, episode_id: str) -> None: ...
    def search(self, session: Session, user_id: str, query: str, k: int = 3) -> list[str]: ...
    def delete(self, episode_id: str) -> None: ...


class LocalEpisodic:
    """Rappel par recouvrement de mots-clés, isolé par `user_id`, sans embeddings.

    Ignore le paramètre `session` (état en mémoire process, pas en base) — signature
    identique au backend pgvector pour rester interchangeable derrière le Protocol.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[tuple[str, str]]] = {}

    def add(self, user_id: str, summary: str, episode_id: str) -> None:
        self._store.setdefault(user_id, []).append((episode_id, summary))

    def search(self, session: Session, user_id: str, query: str, k: int = 3) -> list[str]:
        q = _tokens(query)
        scored: list[tuple[int, str]] = []
        for _, summary in self._store.get(user_id, []):
            score = len(q & _tokens(summary))
            if score > 0:
                scored.append((score, summary))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:k]]

    def delete(self, episode_id: str) -> None:
        for user_id, items in list(self._store.items()):
            self._store[user_id] = [(eid, s) for eid, s in items if eid != episode_id]


class PgVectorEpisodic:
    """Rappel par similarité via `pgvector` : le vecteur vit dans la colonne
    `episode.embedding`, même ligne que le texte — pas de client réseau séparé.
    `add`/`delete` sont des no-op : `db.add_episode`/la suppression de la ligne
    `Episode` font déjà tout le travail, dans la même transaction Postgres."""

    def add(self, user_id: str, summary: str, episode_id: str) -> None:
        pass

    def search(self, session: Session, user_id: str, query: str, k: int = 3) -> list[str]:
        from velmo.memory.db import Episode
        from velmo.memory.embeddings import embed_text

        query_vector = embed_text(query)
        rows = session.scalars(
            select(Episode)
            .where(Episode.user_id == user_id, Episode.embedding.is_not(None))
            .order_by(Episode.embedding.cosine_distance(query_vector))
            .limit(k)
        ).all()
        return [row.summary for row in rows]

    def delete(self, episode_id: str) -> None:
        pass


def get_episodic_backend(db_url: str | None = None) -> EpisodicVectorStore:
    """pgvector si `db_url` (ou `Settings.db_url`) pointe vers un Postgres
    joignable ; sinon `LocalEpisodic`. En production, un Postgres configuré mais
    injoignable lève plutôt que de dégrader silencieusement la mémoire
    épisodique (`require_durable_store`, audit D3-03) ; en dev/CI le repli local
    reste toléré."""
    from velmo.config import require_durable_store
    from velmo.memory.db import _postgres_reachable

    resolved = db_url or get_settings().db_url
    if resolved.startswith("postgresql"):
        if _postgres_reachable(resolved):
            return PgVectorEpisodic()
        require_durable_store("mémoire épisodique", resolved)
    return LocalEpisodic()
