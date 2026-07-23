"""Ajoute la colonne actor à memory_audit (traçabilité : qui a déclenché l'action).

Revision ID: 0004_memory_audit_actor
Revises: 0003_add_rls_guardrails
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_memory_audit_actor"
down_revision = "0003_add_rls_guardrails"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memory_audit",
        sa.Column("actor", sa.String(), nullable=False, server_default="user"),
    )


def downgrade() -> None:
    op.drop_column("memory_audit", "actor")
