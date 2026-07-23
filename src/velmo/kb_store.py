"""Base de connaissances FAQ : backend pgvector (prod) et backend local (hors-ligne).

Les deux exposent `search(query, k) -> list[dict]` renvoyant des extraits sourcés.
"""

from __future__ import annotations

import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select

from velmo.config import get_settings

KB_DOCS_DIR = Path(__file__).resolve().parents[2] / "kb" / "docs"


class KnowledgeBase(Protocol):
    """Interface commune aux backends FAQ (local et Chroma)."""

    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]: ...


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _strip_accents(text.lower())) if len(t) > 2}


def _load_docs(docs_dir: Path) -> list[tuple[str, str]]:
    docs: list[tuple[str, str]] = []
    if docs_dir.is_dir():
        for path in sorted(docs_dir.glob("*.md")):
            docs.append((path.name, path.read_text(encoding="utf-8")))
    return docs


class LocalKB:
    """Recherche locale pondérée par rareté des termes (TF-IDF léger, hors-ligne)."""

    def __init__(self, docs_dir: Path | None = None) -> None:
        self.docs = _load_docs(docs_dir or KB_DOCS_DIR)
        self._indexed = [(src, _tokens(text), text) for src, text in self.docs]
        n = max(len(self._indexed), 1)
        df: dict[str, int] = {}
        for _, toks, _ in self._indexed:
            for tok in toks:
                df[tok] = df.get(tok, 0) + 1
        # Poids IDF : un terme rare (ex. « delai », « reassort ») pèse plus qu'un
        # terme banal (« livraison », « maillot »).
        self._weight = {tok: math.log(1 + n / count) for tok, count in df.items()}

    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        q = _tokens(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for source, toks, text in self._indexed:
            score = sum(self._weight.get(tok, 0.0) for tok in (q & toks))
            if score > 0:
                body = re.sub(r"^#.*\n", "", text).strip()
                scored.append((score, {"source": source, "snippet": body[:300]}))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:k]]


class PgVectorKB:
    """Recherche sémantique FAQ via `pgvector`, même Postgres que le schéma
    métier (`velmo.db.KbArticle`) — pas de service séparé, embeddings
    `intfloat/multilingual-e5-small` (`memory/embeddings.py`)."""

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url

    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        from velmo.db import KbArticle, session_factory
        from velmo.memory.embeddings import embed_text

        query_vector = embed_text(query)
        with session_factory(self._db_url)() as session:
            rows = session.scalars(
                select(KbArticle)
                .where(KbArticle.embedding.is_not(None))
                .order_by(KbArticle.embedding.cosine_distance(query_vector))
                .limit(k)
            ).all()
            return [{"source": row.source, "snippet": row.body[:300]} for row in rows]


def get_kb(db_url: str | None = None) -> KnowledgeBase:
    """pgvector si `db_url` (ou `Settings.db_url`) pointe vers un Postgres
    joignable ; sinon `LocalKB`. Repli gracieux (pas `require_durable_store`) :
    la FAQ est un confort, pas un système critique — contrairement à la mémoire
    épisodique (`memory/episodic.py`, D3-03)."""
    from velmo.db import _postgres_reachable

    resolved = db_url or get_settings().db_url
    if _postgres_reachable(resolved):
        return PgVectorKB(resolved)
    return LocalKB()
