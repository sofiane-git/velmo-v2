"""Schéma relationnel (SQLAlchemy 2) et fabrique de sessions.

Les identifiants sont des chaînes lisibles (ex. `O-2024-0103`, `C-marc-dubois`,
`mu-1999-treble`) pour faciliter le débogage. Les types sont portables : Postgres
en production, SQLite en mémoire pour les tests.
"""

from __future__ import annotations

import enum
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Engine,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
    text,
)
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from velmo.config import get_settings, require_durable_store


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
    # "support" (risque humain : menace, litige) | "security" (risque
    # technique : fuite confirmée, récidive d'injection) — deux destinataires
    # distincts, cf. conception_chantier2_guardrails.md §Que fait l'agent.
    channel: Mapped[str] = mapped_column(String, default="support")
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime(2024, 1, 1))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ToolAudit(Base):
    """Journal des appels d'outils à effet de bord — Ch.4 §A6 (audit Z-01).

    La mémoire avait `memory_audit`, les garde-fous `guardrail_audit`, les
    **actions métier** n'avaient rien — alors que ce sont elles qui déplacent de
    l'argent. Sans ce journal, impossible de répondre à « qui a déclenché ce
    remboursement, sur quelle demande » ni de mesurer les refus, qui sont le
    signal d'abus le plus direct.

    Porte aussi la **clé d'idempotence** (Ch.4 §A5) : `UNIQUE` en base, ce qui
    fait de l'unicité une garantie du store et non d'une vérification
    applicative préalable — celle-ci perdrait la course entre deux appels
    concurrents, qui est exactement le scénario du retry.

    **Append-only** (pas d'`updated_at`) : un appel passé ne se modifie pas.
    **Rétention** : régime `guardrail_audit`, pas `memory_audit` — une trace de
    mouvement d'argent relève de l'obligation comptable et de l'intérêt légitime
    anti-fraude, donc elle survit à une demande d'effacement, anonymisée plutôt
    que détruite. D'où l'absence de FK vers `customers` : une suppression R5 ne
    doit pas emporter le journal en cascade.
    """

    __tablename__ = "tool_audit"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    tool: Mapped[str] = mapped_column(String, index=True)
    # "L" lecture client · "P" lecture publique · "É" écriture réversible ·
    # "I" écriture irréversible (cf. Ch.4 §Inventaire et classification).
    tool_class: Mapped[str] = mapped_column(String)
    # JSON des arguments, **PII masquée** avant écriture : le journal d'actions
    # ne doit pas devenir la copie non filtrée que le reste du système évite.
    arguments: Mapped[str] = mapped_column(Text, default="{}")
    # ok · refused_ownership · refused_state · escalated · capped · replayed · error
    outcome: Mapped[str] = mapped_column(String, default="ok")
    resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Résultat du premier appel, rejoué tel quel sur un doublon (A5).
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Jeton de confirmation consommé — réservé à A4 (confirmation explicite),
    # non encore implémenté : la colonne existe pour que l'ajout de A4 ne soit
    # pas une migration de plus sur une table append-only.
    intent_token: Mapped[str | None] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime(2024, 1, 1))


_EMBEDDING_DIM = 384


class KbArticle(Base):
    """FAQ Velmo (`kb/docs/*.md`) — contenu boutique statique, pas une donnée
    utilisateur : reste dans le schéma métier, pas `velmo.memory.db` (droit à
    l'oubli R1-R6 sans objet ici)."""

    __tablename__ = "kb_article"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(_EMBEDDING_DIM).with_variant(Text(), "sqlite"), nullable=True
    )
    embedding_model_id: Mapped[str | None] = mapped_column(String, nullable=True)


def make_engine(url: str | None = None) -> Engine:
    """Crée un engine SQLAlchemy (Postgres en prod, fourni via `DB_URL`)."""
    if url is None:
        url = get_settings().db_url
    return create_engine(url, future=True)


def _postgres_reachable(url: str, timeout_seconds: int = 1) -> bool:
    """Sonde générique (même logique que `velmo.memory.db._postgres_reachable`,
    dupliquée ici : `velmo.kb_store` ne peut pas importer `velmo.memory.db`,
    contrat d'isolation mémoire, `pyproject.toml`)."""
    if not url.startswith("postgresql"):
        return False
    try:
        probe = create_engine(url, connect_args={"connect_timeout": timeout_seconds})
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        probe.dispose()
        return True
    except Exception:
        return False


def session_factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=make_engine(url), expire_on_commit=False, future=True)


def _default_sqlite_path() -> Path:
    return Path(__file__).resolve().parents[2] / "var" / "velmo_business.db"


def make_business_engine(url: str | None = None) -> Engine:
    """Comme `make_engine`, avec repli SQLite si Postgres est injoignable
    (même convention que `memory/db.py::make_memory_engine` et
    `guardrails/db.py::make_guardrails_engine`) — réservé aux appelants qui
    doivent tolérer ce repli (ex. `mlops.runner._seeded_session`, gate CI en
    mode dégradé hors-ligne). `make_engine` reste la primitive stricte (sans
    repli) utilisée par Alembic (`alembic/env.py`) : une migration ne doit
    jamais basculer silencieusement sur une base différente de sa cible."""
    if url is None:
        url = get_settings().db_url

    if url.startswith("postgresql") and not _postgres_reachable(url):
        require_durable_store("business_db", url)
        warnings.warn(
            f"Postgres injoignable ({url!r}) : repli sur SQLite ({_default_sqlite_path()}).",
            RuntimeWarning,
            stacklevel=2,
        )
        path = _default_sqlite_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{path}", future=True)
    else:
        engine = create_engine(url, future=True)

    if engine.url.drivername.startswith("sqlite"):
        Base.metadata.create_all(engine, checkfirst=True)
    return engine


def fresh_sqlite_session() -> Session:
    """Session SQLite en mémoire avec le schéma créé (tests / évaluation hors-ligne)."""
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()
