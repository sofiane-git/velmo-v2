"""Table `pending_action` : jetons de confirmation d'intention (Ch.4 §A4).

Rend vraie une revendication du Ch.2 qui était fausse : les actions sensibles
étaient censées « rester soumises à confirmation », mais la confirmation était
détectée dans le **texte du message courant** — un seul message pouvait porter la
demande et sa confirmation, donc une injection s'auto-validait.

Contrairement à `tool_audit` (journal conservé, append-only), une intention est un
état **transitoire** : elle vit 15 minutes, et une fois consommée ou expirée elle
n'a plus aucune valeur. Elle est donc purgeable — et doit l'être, puisqu'elle
stocke les arguments en clair (nécessaire pour rejouer l'action confirmée au
caractère près, ce qu'un masquage empêcherait).

Revision ID: 0016_pending_action
Revises: 0015_tool_audit
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_pending_action"
down_revision = "0015_tool_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_action",
        # Le jeton EST la clé primaire : aucun identifiant séparé à corréler, donc
        # aucun chemin où un jeton valide désignerait une autre intention.
        sa.Column("token", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        sa.Column("tool", sa.String(), nullable=False),
        sa.Column("arguments_hash", sa.String(), nullable=False),
        sa.Column("arguments_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("recap", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
    )
    # Index de la recherche du chemin conversationnel (« je confirme » → dernière
    # intention consommable de cet utilisateur) et de la purge.
    op.create_index(
        "ix_pending_action_open",
        "pending_action",
        ["user_id", "consumed_at", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pending_action_open", table_name="pending_action")
    op.drop_table("pending_action")
