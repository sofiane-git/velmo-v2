"""Mémoire épisodique : backend Chroma (prod, embeddings réels) et backend
local (hors-ligne, scoring mots-clés) — même convention prod/offline que
`velmo.kb_store` (`ChromaKB`/`LocalKB`, `get_kb()`).
"""

from __future__ import annotations

import os
import re
import unicodedata
import uuid
from typing import Protocol


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _strip_accents(text.lower())) if len(t) > 2}


class EpisodicStore(Protocol):
    def add(self, user_id: str, summary: str, source_thread_id: str | None) -> str: ...
    def search(self, user_id: str, query: str, k: int = 3) -> list[str]: ...
    def delete(self, episode_id: str) -> None: ...


class LocalEpisodic:
    """Rappel par recouvrement de mots-clés, isolé par `user_id`, sans embeddings."""

    def __init__(self) -> None:
        self._store: dict[str, list[tuple[str, str]]] = {}

    def add(self, user_id: str, summary: str, source_thread_id: str | None) -> str:
        episode_id = f"epi-{uuid.uuid4().hex[:8]}"
        self._store.setdefault(user_id, []).append((episode_id, summary))
        return episode_id

    def search(self, user_id: str, query: str, k: int = 3) -> list[str]:
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


class ChromaEpisodic:
    """Rappel sémantique via ChromaDB, filtré `user_id` en metadata."""

    def __init__(self, collection) -> None:
        self._collection = collection

    def add(self, user_id: str, summary: str, source_thread_id: str | None) -> str:
        episode_id = f"epi-{uuid.uuid4().hex[:8]}"
        self._collection.add(
            ids=[episode_id],
            documents=[summary],
            metadatas=[{"user_id": user_id, "source_thread_id": source_thread_id or ""}],
        )
        return episode_id

    def search(self, user_id: str, query: str, k: int = 3) -> list[str]:
        result = self._collection.query(
            query_texts=[query], n_results=k, where={"user_id": user_id}
        )
        return list(result.get("documents", [[]])[0])

    def delete(self, episode_id: str) -> None:
        self._collection.delete(ids=[episode_id])


def get_episodic_backend() -> EpisodicStore:
    """Chroma réel si `CHROMA_URL` défini, `chromadb` importable et joignable ;
    sinon `LocalEpisodic`. Ne lève jamais — toute erreur de connexion/import
    retombe sur le repli local (même contrat que `kb_store.get_kb()`).
    """
    if not os.getenv("CHROMA_URL"):
        return LocalEpisodic()
    try:
        import chromadb
        from chromadb.utils import embedding_functions
    except ImportError:
        return LocalEpisodic()

    try:
        client = chromadb.HttpClient(host="chroma", port=8000)
        embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
        )
        collection = client.get_or_create_collection("velmo_episodes", embedding_function=embedder)
        return ChromaEpisodic(collection)
    except Exception:
        return LocalEpisodic()
