"""Ingestion de la FAQ Velmo (kb/docs/*.md) dans Postgres (pgvector).

Usage : uv run python scripts/seed_kb.py
Nécessite l'extra `vector` (sentence-transformers) et un Postgres joignable
(`DB_URL`, migré — `alembic upgrade head`).
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from velmo.config import get_settings
from velmo.db import KbArticle, session_factory
from velmo.memory.embeddings import embed_text, embedding_model_id

KB_DOCS_DIR = Path(__file__).resolve().parent.parent / "kb" / "docs"


def main() -> None:
    # Même contrat que `velmo.cli` : en usage CLI, l'environnement vient de
    # `.env` — sans ça, lancé depuis l'hôte, `DB_URL` viserait le hostname
    # conteneur `postgres` et échouerait systématiquement.
    load_dotenv()
    settings = get_settings()
    session_maker = session_factory(settings.db_url)
    model_id = embedding_model_id()

    count = 0
    with session_maker() as session:
        for path in sorted(KB_DOCS_DIR.glob("*.md")):
            body = path.read_text(encoding="utf-8")
            article = session.get(KbArticle, path.stem)
            if article is None:
                article = KbArticle(id=path.stem, source=path.name, body=body)
                session.add(article)
            else:
                article.source = path.name
                article.body = body
            article.embedding = embed_text(body)
            article.embedding_model_id = model_id
            count += 1
        session.commit()

    print(f"FAQ ingérée dans Postgres (pgvector) : {count} documents.")


if __name__ == "__main__":
    main()
