"""Table kb_article : FAQ Velmo en pgvector (remplace Chroma, même Postgres que
le reste du schéma métier). Extension `vector` déjà active depuis 0006.

Revision ID: 0013_kb_articles
Revises: 0012_drift_check_run
Create Date: 2026-07-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0013_kb_articles"
down_revision = "0012_drift_check_run"
branch_labels = None
depends_on = None

_EMBEDDING_DIM = 384


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        embedding_col = sa.Column("embedding", Vector(_EMBEDDING_DIM), nullable=True)
    else:
        embedding_col = sa.Column("embedding", sa.Text(), nullable=True)

    op.create_table(
        "kb_article",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        embedding_col,
        sa.Column("embedding_model_id", sa.String(), nullable=True),
    )

    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_kb_article_embedding_hnsw ON kb_article "
            "USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_kb_article_embedding_hnsw")
    op.drop_table("kb_article")
