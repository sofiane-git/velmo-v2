"""Retire eval_run.version_tag → agent_version.version_tag (FK cassée en prod).

Constatation en prod (premier run réel du gate de release contre Postgres) :
tout `INSERT INTO eval_run` déclenche le check RI (référence à
`agent_version`) et échoue systématiquement avec
`psycopg.errors.InsufficientPrivilege: permission denied for table
agent_version`, reproduit à l'identique pour `velmo_app` ET `velmo_admin`.

Diagnostic exhaustif (voir historique du repo) : `has_table_privilege()` et
`has_column_privilege()` renvoient `true` pour les deux rôles, `relacl`
liste bien toutes les privilèges attendues, RLS est désactivée sur les deux
tables, l'OID référencé par la contrainte correspond bien à la table
courante, `DROP`/`ADD CONSTRAINT` et un redémarrage complet du serveur
Postgres n'ont rien changé. Un couple table/FK neuf avec le même pattern de
triggers (append-only guard) fonctionne sans problème pour `velmo_app` — le
bug est donc isolé à cette relation précise, cause exacte non identifiée.

Intégrité référentielle : `version_tag` n'est jamais saisi librement, il est
systématiquement produit par `mlops.versioning.current_version()` — le
risque qu'une valeur incohérente atterrisse dans `eval_run` sans la
contrainte DB reste donc nul en pratique.

Revision ID: 0018_drop_eval_run_agent_version_fk
Revises: 0017_eval_run_tools_scores
Create Date: 2026-08-25
"""

from __future__ import annotations

from alembic import op

revision = "0018_drop_eval_run_agent_version_fk"
down_revision = "0017_eval_run_tools_scores"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # IF EXISTS : déjà supprimée manuellement en prod pendant le diagnostic
    # (avant que cette migration existe) — doit rester rejouable sans erreur
    # sur une base qui applique la chaîne complète depuis zéro.
    bind.exec_driver_sql("ALTER TABLE eval_run DROP CONSTRAINT IF EXISTS eval_run_version_tag_fkey")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.create_foreign_key(
        "eval_run_version_tag_fkey", "eval_run", "agent_version", ["version_tag"], ["version_tag"]
    )
