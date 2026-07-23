"""Schéma initial Velmo 2.0.

DDL explicite et figé (pas de `Base.metadata.create_all()` sur les modèles
ORM live) : une migration doit rester un instantané immuable de l'état du
schéma à cette date, indépendant de toute évolution ultérieure du code.
`escalations.channel` n'existe pas encore ici — ajoutée par
`0009_escalation_channel`.

Revision ID: 0001_initial
Revises:
Create Date: 2024-05-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

_SEGMENT = sa.Enum("particulier", "pro", "revendeur", name="segment")
_CONDITION = sa.Enum("mint", "neuf", "occasion", name="condition")
_SIZE = sa.Enum("S", "M", "L", "XL", "XXL", name="size")
_ORDER_STATUS = sa.Enum(
    "paid", "prepared", "shipped", "delivered", "cancelled", "returned", name="orderstatus"
)
_RETURN_STATUS = sa.Enum("requested", "accepted", "refused", "refunded", name="returnstatus")
_REFUND_STATUS = sa.Enum("auto", "escalated", "approved", "refused", name="refundstatus")


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("segment", _SEGMENT, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "products",
        sa.Column("ref", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("club", sa.String(), nullable=False),
        sa.Column("season", sa.String(), nullable=False),
        sa.Column("edition", sa.String(), nullable=False),
        sa.Column("condition", _CONDITION, nullable=False),
        sa.Column("base_price", sa.Numeric(10, 2), nullable=False),
    )
    op.create_table(
        "product_variants",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("product_ref", sa.String(), sa.ForeignKey("products.ref"), nullable=False),
        sa.Column("size", _SIZE, nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False, default=0),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("customer_id", sa.String(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("status", _ORDER_STATUS, nullable=False),
        sa.Column("total", sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("shipping_address", sa.JSON(), nullable=False),
    )
    op.create_table(
        "order_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("variant_id", sa.String(), sa.ForeignKey("product_variants.id"), nullable=False),
        sa.Column("size", _SIZE, nullable=False),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False),
    )
    op.create_table(
        "shipments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("carrier", sa.String(), nullable=False),
        sa.Column("tracking_number", sa.String(), nullable=False),
        sa.Column("estimated_delivery", sa.String(), nullable=False),
        sa.Column("actual_delivery", sa.String(), nullable=True),
    )
    op.create_table(
        "returns",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("status", _RETURN_STATUS, nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "refunds",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("status", _REFUND_STATUS, nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "escalations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("customer_id", sa.String(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("escalations")
    op.drop_table("refunds")
    op.drop_table("returns")
    op.drop_table("shipments")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("product_variants")
    op.drop_table("products")
    op.drop_table("customers")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum in (_SEGMENT, _CONDITION, _SIZE, _ORDER_STATUS, _RETURN_STATUS, _REFUND_STATUS):
            enum.drop(bind, checkfirst=True)
