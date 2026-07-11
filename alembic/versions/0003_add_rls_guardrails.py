"""RLS garde-fous : isolation par user_id sur `guardrail_audit`.

Table indépendante de `memory_user`/`memory_audit` (chantier 1) : `user_id`
est une clé logique, pas une FK physique (même découplage que `memory/db.py`
vis-à-vis du schéma métier). Postgres uniquement : sur tout autre dialecte
(SQLite offline) la migration se limite au create_all.

Revision ID: 0003_add_rls_guardrails
Revises: 0002_add_rls_memory
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op

from velmo.guardrails.db import Base as GuardrailsBase

revision = "0003_add_rls_guardrails"
down_revision = "0002_add_rls_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    GuardrailsBase.metadata.create_all(bind)  # idempotent : ne recrée pas l'existant
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
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP POLICY IF EXISTS guardrail_user_isolation ON guardrail_audit")
    op.execute("ALTER TABLE guardrail_audit DISABLE ROW LEVEL SECURITY")
