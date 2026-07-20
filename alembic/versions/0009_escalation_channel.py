"""Colonne channel sur escalations (support vs security — deux canaux
d'escalade distincts, conception_chantier2_guardrails.md).

Revision ID: 0009_escalation_channel
Revises: 0008_guardrail_audit_shadow
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_escalation_channel"
down_revision = "0008_guardrail_audit_shadow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "escalations", sa.Column("channel", sa.String(), nullable=False, server_default="support")
    )


def downgrade() -> None:
    op.drop_column("escalations", "channel")
