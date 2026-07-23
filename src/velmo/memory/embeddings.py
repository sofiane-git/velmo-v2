"""Calcul d'embeddings pour la mémoire épisodique (pgvector) — modèle pinné
(`Settings.embedding_model`), voir conception_chantier1_memoire.md
§Versioning du modèle d'embeddings.
"""

from __future__ import annotations

from functools import lru_cache

from velmo.config import get_settings

EMBEDDING_DIM = 384  # dimension native de intfloat/multilingual-e5-small


@lru_cache(maxsize=1)
def _model() -> object:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(get_settings().embedding_model)


def embed_text(text: str) -> list[float]:
    vector = _model().encode(text, normalize_embeddings=True)  # type: ignore[attr-defined]
    return [float(x) for x in vector]


def embedding_model_id() -> str:
    """Identifiant pinné du modèle, stocké sur chaque épisode — un changement de
    modèle nécessite un ré-embedding batch avant bascule (voir doc)."""
    return get_settings().embedding_model
