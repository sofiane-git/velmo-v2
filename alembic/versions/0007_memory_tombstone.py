"""Table memory_tombstone : bloque la résurrection d'une clé/trigger effacée
par un write extracteur asynchrone tardif (§R5).

DDL explicite et figé (même règle que 0001_initial) — l'ancien
`MemoryBase.metadata.create_all(bind)` référençait les modèles ORM **live** :
toute évolution ultérieure du modèle aurait créé les colonnes « en avance »
ici et cassé la rejouabilité from scratch de la chaîne (collision constatée
sur le même pattern en 0010, cf. audit D6 « chaîne rejouable »).

Revision ID: 0007_memory_tombstone
Revises: 0006_pgvector_episodes
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_memory_tombstone"
down_revision = "0006_pgvector_episodes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_tombstone",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("memory_user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_kind", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "target_kind", "target", name="uq_tombstone_target"),
    )
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE memory_tombstone ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memory_tombstone FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY mem_user_isolation ON memory_tombstone "
        "USING (user_id = current_setting('app.current_user_id', true)) "
        "WITH CHECK (user_id = current_setting('app.current_user_id', true))"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS mem_user_isolation ON memory_tombstone")
    op.execute("DROP TABLE IF EXISTS memory_tombstone")
