"""GRANT manquant sur `pending_action` (Ch.4 §A4).

`0016_pending_action.py` créait la table sans aucun GRANT ni policy RLS,
contrairement à `0002_add_rls_memory.py` (RLS+FORCE+policy) et
`0014_mlops_append_only_grants.py` (`GRANT ... TO CURRENT_USER` explicite).
Révélé par le premier run réel du gate de release contre Postgres :
`psycopg.errors.InsufficientPrivilege: permission denied for table
pending_action` à l'INSERT du jeton de confirmation (A4).

`TO CURRENT_USER` (pas un nom de rôle en dur) : le rôle applicatif diffère
entre environnements (`app` en local/docker-compose, `velmo_app` en
production) — même convention que `0014_mlops_append_only_grants.py`.

Revision ID: 0018_pending_action_grants
Revises: 0017_eval_run_tools_scores
Create Date: 2026-08-25
"""

from __future__ import annotations

from alembic import op

revision = "0018_pending_action_grants"
down_revision = "0017_eval_run_tools_scores"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.exec_driver_sql("GRANT SELECT, INSERT, UPDATE, DELETE ON pending_action TO CURRENT_USER")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.exec_driver_sql(
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON pending_action FROM CURRENT_USER"
    )
