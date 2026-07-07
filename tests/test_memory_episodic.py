from __future__ import annotations

import os

from velmo.memory.episodic import LocalEpisodic, get_episodic_backend


def test_local_episodic_add_and_search_isolated_by_user():
    store = LocalEpisodic()
    store.add("u1", "Litige signale sur commande O-2024-0199", None)
    store.add("u2", "Question sur les frais de port", None)

    results_u1 = store.search("u1", "litige commande", k=3)
    assert any("Litige" in r for r in results_u1)

    results_u2 = store.search("u2", "litige commande", k=3)
    assert all("Litige" not in r for r in results_u2)


def test_local_episodic_delete():
    store = LocalEpisodic()
    episode_id = store.add("u1", "Episode a supprimer", None)
    store.delete(episode_id)
    assert store.search("u1", "Episode", k=3) == []


def test_get_episodic_backend_without_chroma_url_returns_local(monkeypatch):
    monkeypatch.delenv("CHROMA_URL", raising=False)
    backend = get_episodic_backend()
    assert isinstance(backend, LocalEpisodic)


def test_get_episodic_backend_with_chroma_url_falls_back_if_unreachable(monkeypatch):
    monkeypatch.setenv("CHROMA_URL", "http://chroma:8000")
    backend = get_episodic_backend()
    # chromadb non installé par défaut (extra 'vector') ou service injoignable en local
    # -> repli garanti, jamais d'exception propagée.
    assert hasattr(backend, "add") and hasattr(backend, "search") and hasattr(backend, "delete")
