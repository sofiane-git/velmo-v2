"""Outil FAQ : recherche dans la base de connaissances (RAG)."""

from __future__ import annotations

from typing import Any

from ..kb_store import KnowledgeBase


def search_kb(kb: KnowledgeBase, query: str) -> dict[str, Any]:
    """Cherche une réponse dans la FAQ Velmo et renvoie des extraits sourcés."""
    hits = kb.search(query, k=5)
    if not hits:
        return {"found": False, "query": query, "results": []}
    return {"found": True, "query": query, "results": hits}
