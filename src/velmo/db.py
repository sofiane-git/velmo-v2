"""Schéma relationnel (SQLAlchemy 2) et fabrique de sessions.

Les identifiants sont des chaînes lisibles (ex. `O-2024-0103`, `C-marc-dubois`,
`mu-1999-treble`) pour faciliter le débogage. Les types sont portables : Postgres
en production, SQLite en mémoire pour les tests.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Engine,
    Enum,
    ForeignKey,
    Numeric,
    String,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from velmo.config import get_settings


class Base(DeclarativeBase):
    pass


class Segment(str, enum.Enum):
    particulier = "particulier"
    pro = "pro"
    revendeur = "revendeur"


class Condition(str, enum.Enum):
    mint = "mint"
    neuf = "neuf"
    occasion = "occasion"


class Size(str, enum.Enum):
    S = "S"
    M = "M"
    L = "L"
    XL = "XL"
    XXL = "XXL"


class OrderStatus(str, enum.Enum):
    paid = "paid"
    prepared = "prepared"
    shipped = "shipped"
    delivered = "delivered"
    cancelled = "cancelled"
    returned = "returned"


class ReturnStatus(str, enum.Enum):
    requested = "requested"
    accepted = "accepted"
    refused = "refused"
    refunded = "refunded"


class RefundStatus(str, enum.Enum):
    auto = "auto"
    escalated = "escalated"
    approved = "approved"
    refused = "refused"


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    full_name: Mapped[str] = mapped_column(String)
    segment: Mapped[Segment] = mapped_column(Enum(Segment), default=Segment.particulier)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime(2024, 1, 1))
    orders: Mapped[list[Order]] = relationship(back_populates="customer")


class Product(Base):
    __tablename__ = "products"
    ref: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    club: Mapped[str] = mapped_column(String)
    season: Mapped[str] = mapped_column(String)
    edition: Mapped[str] = mapped_column(String, default="")
    condition: Mapped[Condition] = mapped_column(Enum(Condition), default=Condition.neuf)
    base_price: Mapped[float] = mapped_column(Numeric(10, 2))
    variants: Mapped[list[ProductVariant]] = relationship(back_populates="product")


class ProductVariant(Base):
    __tablename__ = "product_variants"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    product_ref: Mapped[str] = mapped_column(ForeignKey("products.ref"))
    size: Mapped[Size] = mapped_column(Enum(Size))
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    stock: Mapped[int] = mapped_column(default=0)
    product: Mapped[Product] = relationship(back_populates="variants")


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.paid)
    total: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime(2024, 1, 1))
    shipping_address: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    variant_id: Mapped[str] = mapped_column(ForeignKey("product_variants.id"))
    size: Mapped[Size] = mapped_column(Enum(Size))
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2))
    order: Mapped[Order] = relationship(back_populates="items")


class Shipment(Base):
    __tablename__ = "shipments"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    carrier: Mapped[str] = mapped_column(String)
    tracking_number: Mapped[str] = mapped_column(String)
    estimated_delivery: Mapped[str] = mapped_column(String, default="")
    actual_delivery: Mapped[str | None] = mapped_column(String, nullable=True)


class Return(Base):
    __tablename__ = "returns"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    reason: Mapped[str] = mapped_column(String)
    status: Mapped[ReturnStatus] = mapped_column(Enum(ReturnStatus), default=ReturnStatus.requested)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime(2024, 1, 1))


class Refund(Base):
    __tablename__ = "refunds"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    reason: Mapped[str] = mapped_column(String)
    status: Mapped[RefundStatus] = mapped_column(Enum(RefundStatus), default=RefundStatus.auto)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime(2024, 1, 1))


class Escalation(Base):
    __tablename__ = "escalations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    reason: Mapped[str] = mapped_column(String)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime(2024, 1, 1))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


def make_engine(url: str | None = None) -> Engine:
    """Crée un engine SQLAlchemy (Postgres en prod, fourni via `DB_URL`)."""
    if url is None:
        url = get_settings().db_url
    return create_engine(url, future=True)


def session_factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=make_engine(url), expire_on_commit=False, future=True)


def fresh_sqlite_session() -> Session:
    """Session SQLite en mémoire avec le schéma créé (tests / évaluation hors-ligne)."""
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()
