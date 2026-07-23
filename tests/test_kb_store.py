"""KB FAQ : repli local (hors-ligne) ; le backend pgvector est exercé via
Postgres réel (voir tests/acceptance), pas ici — même convention que
tests/test_memory_episodic.py pour PgVectorEpisodic."""

from __future__ import annotations

from velmo.kb_store import LocalKB, get_kb


def test_local_kb_finds_relevant_doc(tmp_path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "livraison.md").write_text(
        "# Livraison\nLe délai de livraison standard est de 3 à 5 jours ouvrés.",
        encoding="utf-8",
    )
    (docs_dir / "retours.md").write_text(
        "# Retours\nLes retours sont acceptés sous 14 jours.", encoding="utf-8"
    )
    kb = LocalKB(docs_dir)
    results = kb.search("quel est le délai de livraison", k=1)
    assert results
    assert results[0]["source"] == "livraison.md"


def test_local_kb_returns_empty_without_match(tmp_path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "livraison.md").write_text("# Livraison\ncontenu.", encoding="utf-8")
    kb = LocalKB(docs_dir)
    assert kb.search("xyzabc123 sans rapport", k=5) == []


def test_get_kb_falls_back_to_local_without_postgres() -> None:
    # Même convention que `get_episodic_backend` (tests/test_memory_episodic.py) :
    # un `db_url` explicite en SQLite retombe sur le backend local, pas de Postgres réel requis.
    kb = get_kb(db_url="sqlite:///:memory:")
    assert isinstance(kb, LocalKB)
