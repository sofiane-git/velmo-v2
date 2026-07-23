"""Table memory_tombstone : bloque la résurrection d'une clé/trigger effacée
par un write extracteur asynchrone tardif (§R5).

Revision ID: 0007_memory_tombstone
Revises: 0006_pgvector_episodes
Create Date: 2026-07-17
"""

from __future__ import annotations

from alembic import op

from velmo.memory.db import Base as MemoryBase

revision = "0007_memory_tombstone"
down_revision = "0006_pgvector_episodes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    MemoryBase.metadata.create_all(bind)
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
