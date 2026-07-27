"""Colonnes `note_tools` / `tool_selection_accuracy` sur `eval_run` (Ch.4 §Évaluation).

La couche qui engage de l'argent était la seule dimension sans note. Deux colonnes
distinctes, parce que les deux mesures n'ont pas le même statut :

- `note_tools` — cas **déterministes** (refus corrects, aucune action irréversible
  sans confirmation). Entre dans `min(dims)`, donc **bloque** une livraison.
- `tool_selection_accuracy` — justesse de sélection d'outil, **reporting seul** :
  du bruit de routage n'a pas à bloquer une livraison (M4).

Les deux sont **nullable** : les runs antérieurs à leur introduction n'ont pas
mesuré ça, et un `0.0` rétroactif se lirait comme un échec plutôt que comme une
absence de mesure — la même distinction que le rapport applique déjà entre « non
mesuré » et zéro.

`ALTER TABLE ADD COLUMN` est du DDL : il n'est pas concerné par les triggers
append-only posés en 0014 (qui portent sur `UPDATE`/`DELETE`/`TRUNCATE` de lignes).

Revision ID: 0017_eval_run_tools_scores
Revises: 0016_pending_action
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_eval_run_tools_scores"
down_revision = "0016_pending_action"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("eval_run", sa.Column("note_tools", sa.Float(), nullable=True))
    op.add_column("eval_run", sa.Column("tool_selection_accuracy", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("eval_run", "tool_selection_accuracy")
    op.drop_column("eval_run", "note_tools")
