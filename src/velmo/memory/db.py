"""Schéma relationnel de la mémoire (propre à ce sous-système, séparé du schéma
métier de `velmo.db`) et résolution de l'engine : Postgres réel si joignable
(même convention que `db.py` business), sinon repli fichier SQLite pour rester
utilisable hors-ligne (même esprit que `kb_store.get_kb()`).
"""

from __future__ import annotations

import os
import sqlite3
import uuid
import warnings
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    Engine,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class MemoryUser(Base):
    __tablename__ = "memory_user"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    locale: Mapped[str] = mapped_column(String, default="fr")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Conversation(Base):
    __tablename__ = "conversation"
    thread_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("memory_user.user_id", ondelete="CASCADE"))
    summary: Mapped[str] = mapped_column(Text, default="")
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    summarized_up_to_turn: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_message_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Message(Base):
    __tablename__ = "message"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("conversation.thread_id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("memory_user.user_id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    turn: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Fact(Base):
    __tablename__ = "fact"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("memory_user.user_id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    source_thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_fact_user_key"),)


class Procedure(Base):
    __tablename__ = "procedure"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("memory_user.user_id", ondelete="CASCADE"))
    trigger: Mapped[str] = mapped_column(String)
    rule: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source_thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "trigger", name="uq_procedure_user_trigger"),)


class Episode(Base):
    __tablename__ = "episode"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("memory_user.user_id", ondelete="CASCADE"))
    summary: Mapped[str] = mapped_column(Text)
    chroma_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MemoryAudit(Base):
    __tablename__ = "memory_audit"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("memory_user.user_id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String)
    target: Mapped[str] = mapped_column(String)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def _default_sqlite_path() -> Path:
    return Path(__file__).resolve().parents[3] / "var" / "velmo_memory.db"


def _postgres_reachable(url: str, timeout_seconds: int = 1) -> bool:
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


def make_memory_engine(url: str | None = None) -> Engine:
    """Postgres réel si `url` (ou `DB_URL`) est joignable ; sinon SQLite fichier
    persistant (`var/velmo_memory.db`) — jamais `:memory:` par défaut, pour que
    deux `MemoryManager()` séparés partagent le même état (R2).

    Une `url` explicite non-Postgres (ex. `sqlite:///:memory:`) est toujours
    utilisée telle quelle, sans sonde ni repli : c'est le point d'entrée des
    tests qui veulent une base isolée. Une `url` explicite Postgres est en
    revanche sondée comme le chemin par défaut : si elle est injoignable, on
    retombe aussi sur le fichier SQLite partagé (avec un avertissement, pas
    silencieusement).
    """
    if url is not None:
        if url.startswith("postgresql") and not _postgres_reachable(url):
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
    else:
        pg_url = os.getenv("DB_URL", "postgresql+psycopg://app:app@localhost:5432/velmo")
        if _postgres_reachable(pg_url):
            engine = create_engine(pg_url, future=True)
        else:
            warnings.warn(
                f"Postgres injoignable ({pg_url!r}) : repli sur SQLite ({_default_sqlite_path()}).",
                RuntimeWarning,
                stacklevel=2,
            )
            path = _default_sqlite_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(f"sqlite:///{path}", future=True)

    if engine.url.drivername.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_connection: object, connection_record: object) -> None:
            if isinstance(dbapi_connection, sqlite3.Connection):
                dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine
