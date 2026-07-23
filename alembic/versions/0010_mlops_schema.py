"""Tables MLOps : agent_version, eval_run, eval_case_result.

DDL explicite et figé à l'état du schéma du 2026-07-17 — avant
`0011_gate_config_hash` (la colonne n'existe PAS ici) et avant
`0012_drift_check_run` (la table n'existe PAS ici). L'ancien
`MlopsBase.metadata.create_all()` référençait les modèles ORM **live** :
dès que le modèle a gagné `gate_config_hash` (0011) et `DriftCheckRun`
(0012), un replay from scratch les créait « en avance » ici puis 0011/0012
échouaient (`DuplicateColumn`/`DuplicateTable`) — chaîne non rejouable
(audit D6). Même règle que 0001_initial : une migration est un instantané
immuable, jamais un miroir du code vivant.

Revision ID: 0010_mlops_schema
Revises: 0009_escalation_channel
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_mlops_schema"
down_revision = "0009_escalation_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_version",
        sa.Column("version_tag", sa.String(), primary_key=True),
        sa.Column("prompt_hash", sa.String(), nullable=False),
        sa.Column("memory_config_hash", sa.String(), nullable=False),
        sa.Column("guardrail_config_hash", sa.String(), nullable=False),
        # gate_config_hash : ajouté par 0011, volontairement absent ici.
        sa.Column("git_commit", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "eval_run",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "version_tag",
            sa.String(),
            sa.ForeignKey("agent_version.version_tag"),
            nullable=False,
        ),
        sa.Column("note_memory", sa.Float(), nullable=False),
        sa.Column("note_guardrails", sa.Float(), nullable=False),
        sa.Column("note_quality", sa.Float(), nullable=False),
        sa.Column("note_globale", sa.Float(), nullable=False),
        sa.Column("global_gate", sa.Float(), nullable=False),
        sa.Column("gate_passed", sa.Boolean(), nullable=False),
        sa.Column("block_rate", sa.Float(), nullable=False),
        sa.Column("false_positive_rate", sa.Float(), nullable=False),
        sa.Column("latency_p50_ms", sa.Float(), nullable=False),
        sa.Column("latency_p95_ms", sa.Float(), nullable=False),
        sa.Column("cost_per_conv", sa.Float(), nullable=False),
        sa.Column("langfuse_trace_url", sa.String(), nullable=True),
        sa.Column("ran_at", sa.DateTime(), nullable=False),
        sa.Column("triggered_by", sa.String(), nullable=False),
    )
    op.create_table(
        "eval_case_result",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("eval_run.id"), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("suite", sa.String(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("retried", sa.Boolean(), nullable=False),
        sa.Column("error_kind", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("eval_case_result")
    op.drop_table("eval_run")
    op.drop_table("agent_version")
