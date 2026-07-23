"""Ingestion de la FAQ Velmo (kb/docs/*.md) dans Chroma.

Usage : uv run python scripts/seed_kb.py
Nécessite l'extra `vector` (chromadb + sentence-transformers) et un service Chroma.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

from dotenv import load_dotenv

KB_DOCS_DIR = Path(__file__).resolve().parent.parent / "kb" / "docs"


def main() -> None:
    import chromadb
    from chromadb.utils import embedding_functions

    # Même contrat que `velmo.cli` : en usage CLI, l'environnement vient de
    # `.env` — sans ça, lancé depuis l'hôte, le script visait le hostname
    # conteneur `chroma` et échouait systématiquement (revue tuto dev).
    load_dotenv()
    # CHROMA_URL = la variable canonique (celle que lit `velmo.kb_store`) —
    # plus de couple CHROMA_HOST/CHROMA_PORT divergent.
    url = urlparse(os.getenv("CHROMA_URL", "http://localhost:8000"))
    client = chromadb.HttpClient(host=url.hostname or "localhost", port=url.port or 8000)
    embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
    )
    collection = client.get_or_create_collection(
        "velmo_faq",
        embedding_function=embedder,  # type: ignore[arg-type]
    )

    docs, ids, metas = [], [], []
    for path in sorted(KB_DOCS_DIR.glob("*.md")):
        docs.append(path.read_text(encoding="utf-8"))
        ids.append(path.stem)
        metas.append({"source": path.name})

    collection.upsert(documents=docs, ids=ids, metadatas=metas)
    print(f"FAQ ingérée dans Chroma : {len(docs)} documents.")


if __name__ == "__main__":
    main()
