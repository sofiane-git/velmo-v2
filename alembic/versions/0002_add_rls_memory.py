"""RLS mémoire : isolation par user_id sur les 7 tables mémoire.

Crée d'abord les tables mémoire (absentes du schéma business ciblé par Alembic),
puis active Row-Level Security + policies. Postgres uniquement : sur tout autre
dialecte (SQLite offline) la migration se limite au create_all.

Revision ID: 0002_add_rls_memory
Revises: 0001_initial
Create Date: 2026-07-08
"""

from __future__ import annotations

from alembic import op

from velmo.memory.db import Base as MemoryBase

revision = "0002_add_rls_memory"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_TABLES = (
    "memory_user",
    "conversation",
    "message",
    "fact",
    "procedure",
    "episode",
    "memory_audit",
)


def upgrade() -> None:
    bind = op.get_bind()
    MemoryBase.metadata.create_all(bind)  # idempotent : ne recrée pas l'existant
    if bind.dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mem_user_isolation ON {table} "
            "USING (user_id = current_setting('app.current_user_id', true))"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS mem_user_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
