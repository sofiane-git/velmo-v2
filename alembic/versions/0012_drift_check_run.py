"""Table drift_check_run : historique des runs de drift ciblés (audit D8-03 —
la règle « deux nuits consécutives » de la conception ch.3 §Rollback exige un
historique persistant, hors gate EvalRun dont les 3 notes sont NOT NULL).

Revision ID: 0012_drift_check_run
Revises: 0011_gate_config_hash
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_drift_check_run"
down_revision = "0011_gate_config_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drift_check_run",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("suite", sa.String(), nullable=False),
        sa.Column("cases", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Integer(), nullable=False),
        sa.Column("note", sa.Float(), nullable=False),
        sa.Column("ran_at", sa.DateTime(), nullable=False),
        sa.Column("triggered_by", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("drift_check_run")
