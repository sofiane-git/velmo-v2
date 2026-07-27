"""Table `tool_audit` : journal des actions métier + clé d'idempotence (Ch.4 §A5/A6).

Comble le trou le plus coûteux du dispositif : chaque outil d'écriture générait un
identifiant neuf et committait, sans contrainte en base — un retry produisait
**deux remboursements**. La contrainte `UNIQUE` sur `idempotency_key` porte la
garantie côté store, là où une vérification applicative préalable perdrait la
course entre deux appels concurrents (le scénario même du retry).

Append-only, comme les deux autres journaux du projet. Rétention alignée sur
`guardrail_audit` (obligation comptable / anti-fraude) et **non** sur
`memory_audit` : pas de FK vers `customers`, pour qu'une suppression R5 n'emporte
pas la trace des mouvements d'argent en cascade.

Revision ID: 0015_tool_audit
Revises: 0014_mlops_append_only_grants
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_tool_audit"
down_revision = "0014_mlops_append_only_grants"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return bool(op.get_bind().dialect.name == "postgresql")


def upgrade() -> None:
    op.create_table(
        "tool_audit",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        sa.Column("tool", sa.String(), nullable=False, index=True),
        sa.Column("tool_class", sa.String(), nullable=False),
        sa.Column("arguments", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("outcome", sa.String(), nullable=False, server_default="ok"),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("intent_token", sa.String(), nullable=True),
        # Nullable **et** unique : une ligne de rejeu, ou une issue sans effet,
        # ne porte pas de clé (plusieurs NULL sont permis par la contrainte).
        sa.Column("idempotency_key", sa.String(), nullable=True, unique=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("source_thread_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    if not _is_postgres():
        return
    bind = op.get_bind()
    # La fonction de garde est désormais partagée par deux chantiers (résultats
    # d'évaluation, journal d'actions) : son message ne doit plus citer un
    # chapitre précis, sinon l'erreur renvoyée en production désigne la mauvaise
    # conception. `CREATE OR REPLACE` : idempotent, et met à jour les bases où
    # 0014 est déjà appliquée.
    bind.exec_driver_sql(
        """
        CREATE OR REPLACE FUNCTION velmo_append_only_guard() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION USING
                MESSAGE = 'Table ' || TG_TABLE_NAME || ' est append-only : '
                          || 'operation ' || TG_OP || ' interdite.',
                HINT = 'Un evenement deja journalise ne se reecrit pas ; ajoutez '
                       || 'une nouvelle ligne plutot que de modifier celle-ci.';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    # Append-only par trigger, pas par REVOKE : mesuré en 0014, le rôle
    # applicatif est superuser/propriétaire, donc les privilèges sont contournés.
    # Une ligne d'audit **complétée** après l'action est la seule exception
    # légitime à l'immutabilité, et elle a lieu dans la transaction de l'appel :
    # on protège donc contre `DELETE`/`TRUNCATE`, pas contre `UPDATE`.
    bind.exec_driver_sql("DROP TRIGGER IF EXISTS tool_audit_append_only_row ON tool_audit")
    bind.exec_driver_sql(
        "CREATE TRIGGER tool_audit_append_only_row BEFORE DELETE ON tool_audit "
        "FOR EACH ROW EXECUTE FUNCTION velmo_append_only_guard()"
    )
    bind.exec_driver_sql("DROP TRIGGER IF EXISTS tool_audit_append_only_truncate ON tool_audit")
    bind.exec_driver_sql(
        "CREATE TRIGGER tool_audit_append_only_truncate BEFORE TRUNCATE ON tool_audit "
        "FOR EACH STATEMENT EXECUTE FUNCTION velmo_append_only_guard()"
    )


def downgrade() -> None:
    if _is_postgres():
        bind = op.get_bind()
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS tool_audit_append_only_row ON tool_audit")
        bind.exec_driver_sql("DROP TRIGGER IF EXISTS tool_audit_append_only_truncate ON tool_audit")
    op.drop_table("tool_audit")
