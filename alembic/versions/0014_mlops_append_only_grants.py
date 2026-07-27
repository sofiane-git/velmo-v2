"""Append-only des tables MLOps : garantie de base, plus une convention (audit Z-10).

La conception ch.3 §Qu'est-ce qu'une version présentait l'append-only comme une
propriété des tables (« append-only forcé côté base »), tout en signalant qu'aucun
artefact ne le posait : le rôle applicatif pouvait donc `UPDATE`/`DELETE` un
résultat d'évaluation déjà publié. Une revendication de garantie sans artefact
vérifiable est exactement ce que la règle de rédaction interdit — voici l'artefact.

Ce que ça protège : `eval_run` et `eval_case_result` sont la **source de vérité du
pass/fail** d'une version livrée, et `agent_version` son identité. Pouvoir les
réécrire a posteriori rendrait l'historique de qualité non opposable (une
régression effaçable après coup, un hash de version réaffecté).

**Pourquoi des triggers et pas seulement des `REVOKE`.** Première tentative :
`REVOKE UPDATE, DELETE, TRUNCATE`. Mesuré sur un Postgres réel — l'ACL changeait
bien (plus d'`UPDATE` dans `information_schema.role_table_grants`) et un `UPDATE`
**réussissait quand même** : le rôle applicatif du déploiement est
`superuser` **et** propriétaire des tables, or un superuser contourne tout
contrôle de privilège. Un `REVOKE` seul aurait donc produit une garantie
cosmétique — précisément le défaut que ce lot corrige. Les triggers, eux,
s'exécutent quel que soit le rôle.

Les `REVOKE` sont conservés en défense en profondeur : ils portent la garantie sur
un déploiement où l'application tourne (correctement) avec un rôle non privilégié.

No-op hors PostgreSQL : SQLite (repli hors-ligne/tests) n'a ni rôles ni triggers
d'instruction — l'append-only y reste une convention applicative, acceptable pour
un store non durable qui ne sert jamais de référence.

Revision ID: 0014_mlops_append_only_grants
Revises: 0013_kb_articles
Create Date: 2026-07-27
"""

from __future__ import annotations

from alembic import op

revision = "0014_mlops_append_only_grants"
down_revision = "0013_kb_articles"
branch_labels = None
depends_on = None

APPEND_ONLY_TABLES = ("agent_version", "eval_run", "eval_case_result")

# Message construit par concaténation plutôt qu'avec les substitutions `%` de
# `RAISE` : le driver (psycopg) interprète `%` comme un placeholder de requête et
# refuse la définition.
_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION velmo_append_only_guard() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION USING
        MESSAGE = 'Table ' || TG_TABLE_NAME || ' est append-only (conception ch.3) : '
                  || 'operation ' || TG_OP || ' interdite.',
        HINT = 'Un resultat d''evaluation publie ne se reecrit pas ; ajoutez un '
               || 'nouveau run plutot que de modifier celui-ci.';
END;
$$ LANGUAGE plpgsql;
"""


def _is_postgres() -> bool:
    return bool(op.get_bind().dialect.name == "postgresql")


def upgrade() -> None:
    if not _is_postgres():
        return
    bind = op.get_bind()
    bind.exec_driver_sql(_GUARD_FUNCTION)
    for table in APPEND_ONLY_TABLES:
        # Défense en profondeur (efficace seulement si le rôle applicatif n'est
        # ni superuser ni propriétaire — voir l'en-tête). `TRUNCATE` est un
        # privilège distinct de `DELETE` : le laisser suffirait à vider une table
        # sans jamais exécuter de `DELETE`.
        bind.exec_driver_sql(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM CURRENT_USER")
        bind.exec_driver_sql(f"GRANT INSERT, SELECT ON {table} TO CURRENT_USER")
        # La garantie réelle : les triggers s'appliquent à tous les rôles.
        bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {table}_append_only_row ON {table}")
        bind.exec_driver_sql(
            f"CREATE TRIGGER {table}_append_only_row "
            f"BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION velmo_append_only_guard()"
        )
        # `TRUNCATE` ne déclenche aucun trigger de ligne : il lui faut son propre
        # trigger d'instruction, sans quoi la table reste videable.
        bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {table}_append_only_truncate ON {table}")
        bind.exec_driver_sql(
            f"CREATE TRIGGER {table}_append_only_truncate "
            f"BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION velmo_append_only_guard()"
        )


def downgrade() -> None:
    if not _is_postgres():
        return
    bind = op.get_bind()
    for table in APPEND_ONLY_TABLES:
        bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {table}_append_only_row ON {table}")
        bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {table}_append_only_truncate ON {table}")
        bind.exec_driver_sql(f"GRANT UPDATE, DELETE, TRUNCATE ON {table} TO CURRENT_USER")
    bind.exec_driver_sql("DROP FUNCTION IF EXISTS velmo_append_only_guard()")
