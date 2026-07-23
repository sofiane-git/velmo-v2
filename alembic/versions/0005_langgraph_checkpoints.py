"""Remplace conversation/message par thread (en-tête léger) + checkpoints
LangGraph. Le fil de messages n'est plus une table applicative — il vit dans
les tables gérées par PostgresSaver (créées par `checkpointer.setup()`, pas
par cette migration : voir `memory.graph.get_checkpointer`).

Revision ID: 0005_langgraph_checkpoints
Revises: 0004_memory_audit_actor
Create Date: 2026-07-17
"""

from __future__ import annotations

from alembic import op

revision = "0005_langgraph_checkpoints"
down_revision = "0004_memory_audit_actor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP POLICY IF EXISTS mem_user_isolation ON message")
    op.execute("DROP POLICY IF EXISTS mem_user_isolation ON conversation")
    op.rename_table("conversation", "thread")
    op.drop_column("thread", "summarized_up_to_turn")
    op.drop_table("message")
    op.execute(
        "CREATE POLICY mem_user_isolation ON thread "
        "USING (user_id = current_setting('app.current_user_id', true)) "
        "WITH CHECK (user_id = current_setting('app.current_user_id', true))"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade non supporté : le fil de messages a migré vers les checkpoints "
        "LangGraph, irréversible sans perte de données."
    )
