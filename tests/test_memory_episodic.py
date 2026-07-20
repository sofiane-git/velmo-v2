"""Mémoire épisodique : repli local (hors-ligne) ; le backend pgvector est
exercé via tests/test_memory_manager.py (nécessite Postgres réel)."""

from __future__ import annotations

from velmo.memory.episodic import LocalEpisodic, get_episodic_backend


def test_local_episodic_recalls_by_keyword_overlap() -> None:
    store = LocalEpisodic()
    store.add("u1", "Litige authenticité maillot Milan 1994", "epi-1")
    store.add("u1", "Question sur la taille des shorts", "epi-2")
    results = store.search(None, "u1", "litige sur un maillot", k=1)  # type: ignore[arg-type]
    assert results == ["Litige authenticité maillot Milan 1994"]


def test_local_episodic_isolated_by_user() -> None:
    store = LocalEpisodic()
    store.add("marc", "Commande O-2024-0103 en litige", "epi-1")
    store.add("sophie", "Commande O-2024-0107 en litige", "epi-2")
    results = store.search(None, "sophie", "litige commande", k=5)  # type: ignore[arg-type]
    assert results == ["Commande O-2024-0107 en litige"]
    assert "O-2024-0103" not in "".join(results)


def test_get_episodic_backend_falls_back_to_local_without_postgres(monkeypatch) -> None:
    # `get_episodic_backend` ne doit plus dépendre de `chroma_url` : sans Postgres
    # joignable (SQLite de test), il retombe sur LocalEpisodic.
    store = get_episodic_backend(db_url="sqlite:///:memory:")
    assert isinstance(store, LocalEpisodic)
