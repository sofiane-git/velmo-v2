"""Colonne embedding (pgvector) + embedding_model_id sur episode ; retire
chroma_id. Active l'extension vector et l'index HNSW (Postgres uniquement).

Revision ID: 0006_pgvector_episodes
Revises: 0005_langgraph_checkpoints
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0006_pgvector_episodes"
down_revision = "0005_langgraph_checkpoints"
branch_labels = None
depends_on = None

_EMBEDDING_DIM = 384


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.add_column("episode", sa.Column("embedding", Vector(_EMBEDDING_DIM), nullable=True))
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_episode_embedding_hnsw ON episode "
            "USING hnsw (embedding vector_cosine_ops)"
        )
    else:
        op.add_column("episode", sa.Column("embedding", sa.Text(), nullable=True))
    op.add_column("episode", sa.Column("embedding_model_id", sa.String(), nullable=True))
    op.drop_column("episode", "chroma_id")


def downgrade() -> None:
    op.add_column("episode", sa.Column("chroma_id", sa.String(), nullable=True))
    op.drop_column("episode", "embedding_model_id")
    op.drop_column("episode", "embedding")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_episode_embedding_hnsw")
