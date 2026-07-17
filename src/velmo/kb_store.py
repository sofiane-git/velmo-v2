"""Base de connaissances FAQ : backend Chroma (prod) et backend local (hors-ligne).

Les deux exposent `search(query, k) -> list[dict]` renvoyant des extraits sourcés.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

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


class ChromaKB:
    """Recherche sémantique via Chroma + embeddings multilingues e5."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        result = self._collection.query(query_texts=[query], n_results=k)
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        return [
            {"source": (meta or {}).get("source", "kb"), "snippet": doc}
            for doc, meta in zip(docs, metas)
        ]


def get_kb() -> KnowledgeBase:
    """Renvoie le backend Chroma si configuré et disponible, sinon le backend local."""
    settings = get_settings()
    chroma_url = settings.chroma_url
    if not chroma_url:
        return LocalKB()
    try:
        import chromadb
        from chromadb.utils import embedding_functions
    except ImportError:
        return LocalKB()

    try:
        parsed = urlparse(chroma_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 8000
        client = chromadb.HttpClient(host=host, port=port)
        embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=settings.embedding_model
        )
        # Le stub chromadb type SentenceTransformerEmbeddingFunction plus étroitement
        # (float32) que EmbeddingFunction générique attendu ici (float64) : incompatibilité
        # de stub, pas d'erreur runtime.
        collection = client.get_or_create_collection(
            "velmo_faq", embedding_function=embedder  # type: ignore[arg-type]
        )
        return ChromaKB(collection)
    except Exception as exc:
        logging.warning("[kb] Chroma indisponible (%s) — fallback sur LocalKB.", exc)
        return LocalKB()
