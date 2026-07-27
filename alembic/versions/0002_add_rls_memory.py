"""RLS mémoire : isolation par user_id sur les 7 tables mémoire.

Crée d'abord les tables mémoire (absentes du schéma business ciblé par Alembic),
puis active Row-Level Security + policies. Postgres uniquement pour la partie
RLS (sur tout autre dialecte les tables sont créées quand même, pas les
policies).

DDL explicite et figé (pas de `Base.metadata.create_all()` sur les modèles
ORM live) : reflète la forme des tables telle qu'elle était à cette date —
`conversation` (pas encore renommée `thread`), `episode.chroma_id` (pas
encore `embedding`), `memory_audit` sans `actor` — les migrations
`0004`-`0007` les feront évoluer.

Revision ID: 0002_add_rls_memory
Revises: 0001_initial
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

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
    op.create_table(
        "memory_user",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("locale", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "conversation",
        sa.Column("thread_id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("memory_user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("summarized_up_to_turn", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "message",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "thread_id",
            sa.String(),
            sa.ForeignKey("conversation.thread_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("memory_user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "fact",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("memory_user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_thread_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "key", name="uq_fact_user_key"),
    )
    op.create_table(
        "procedure",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("memory_user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("source_thread_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "trigger", name="uq_procedure_user_trigger"),
    )
    op.create_table(
        "episode",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("memory_user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("chroma_id", sa.String(), nullable=True),
        sa.Column("source_thread_id", sa.String(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "memory_audit",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("memory_user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE : applique la policy même au rôle propriétaire de la table (sinon RLS
        # est silencieusement ignorée pour ce rôle, y compris celui utilisé par l'appli).
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mem_user_isolation ON {table} "
            "USING (user_id = current_setting('app.current_user_id', true)) "
            "WITH CHECK (user_id = current_setting('app.current_user_id', true))"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _TABLES:
            op.execute(f"DROP POLICY IF EXISTS mem_user_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("memory_audit")
    op.drop_table("episode")
    op.drop_table("procedure")
    op.drop_table("fact")
    op.drop_table("message")
    op.drop_table("conversation")
    op.drop_table("memory_user")
