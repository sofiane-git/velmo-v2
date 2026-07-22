"""RLS garde-fous : isolation par user_id sur `guardrail_audit`.

Table indépendante de `memory_user`/`memory_audit` (chantier 1) : `user_id`
est une clé logique, pas une FK physique (même découplage que `memory/db.py`
vis-à-vis du schéma métier). Postgres uniquement pour la partie RLS.

DDL explicite et figé (pas de `Base.metadata.create_all()` sur les modèles
ORM live) : `shadow_verdict` n'existe pas encore ici — ajoutée par
`0008_guardrail_audit_shadow`.

Revision ID: 0003_add_rls_guardrails
Revises: 0002_add_rls_memory
Create Date: 2026-07-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_add_rls_guardrails"
down_revision = "0002_add_rls_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guardrail_audit",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("location", sa.String(), nullable=False),  # "input" | "output"
        sa.Column("method", sa.String(), nullable=False),  # regex | classifier | llm_judge | ...
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),  # "block" | "flag"
        sa.Column("source_thread_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE guardrail_audit ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE guardrail_audit FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY guardrail_user_isolation ON guardrail_audit "
        "USING (user_id = current_setting('app.current_user_id', true)) "
        "WITH CHECK (user_id = current_setting('app.current_user_id', true))"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS guardrail_user_isolation ON guardrail_audit")
        op.execute("ALTER TABLE guardrail_audit DISABLE ROW LEVEL SECURITY")
    op.drop_table("guardrail_audit")
