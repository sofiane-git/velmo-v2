"""Colonne shadow_verdict sur guardrail_audit — verdict RuleBasedJudge calculé
en continu (shadow mode), comparé au juge cloud sans jamais influencer la
décision (§Spécification du RuleBasedJudge, conception_chantier2_guardrails.md).

Revision ID: 0008_guardrail_audit_shadow
Revises: 0007_memory_tombstone
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_guardrail_audit_shadow"
down_revision = "0007_memory_tombstone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guardrail_audit", sa.Column("shadow_verdict", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("guardrail_audit", "shadow_verdict")
