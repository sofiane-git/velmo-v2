"""agent_version.gate_config_hash : seuils de gate hashés dans l'identité de
version (audit D8-05 — conception ch.3 §Seuils : « chiffres versionnés dans un
fichier de config, donc hashés dans la version »).

Revision ID: 0011_gate_config_hash
Revises: 0010_mlops_schema
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_gate_config_hash"
down_revision = "0010_mlops_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable : les versions enregistrées avant cette migration n'ont pas de
    # hash de gate — pas de backfill (l'identité d'une version passée ne se
    # réécrit pas).
    op.add_column("agent_version", sa.Column("gate_config_hash", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_version", "gate_config_hash")
