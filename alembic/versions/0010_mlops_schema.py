"""Tables MLOps : agent_version, eval_run, eval_case_result.

Revision ID: 0010_mlops_schema
Revises: 0009_escalation_channel
Create Date: 2026-07-17
"""

from __future__ import annotations

from alembic import op

from velmo.mlops.db import Base as MlopsBase

revision = "0010_mlops_schema"
down_revision = "0009_escalation_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    MlopsBase.metadata.create_all(op.get_bind())


def downgrade() -> None:
    MlopsBase.metadata.drop_all(op.get_bind())
