"""Schéma relationnel de la mémoire (propre à ce sous-système, séparé du schéma
métier de `velmo.db`) et résolution de l'engine : Postgres réel si joignable
(même convention que `db.py` business), sinon repli fichier SQLite pour rester
utilisable hors-ligne (même esprit que `kb_store.get_kb()`).
"""

from __future__ import annotations

import os
import sqlite3
import unicodedata
import uuid
import warnings
from datetime import datetime, timedelta, timezone
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
    or_,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class MemoryUser(Base):
    __tablename__ = "memory_user"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    locale: Mapped[str] = mapped_column(String, default="fr")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Conversation(Base):
    __tablename__ = "conversation"
    thread_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("memory_user.user_id", ondelete="CASCADE"))
    summary: Mapped[str] = mapped_column(Text, default="")
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    summarized_up_to_turn: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_message_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Message(Base):
    __tablename__ = "message"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("conversation.thread_id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("memory_user.user_id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    turn: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Fact(Base):
    __tablename__ = "fact"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("memory_user.user_id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    source_thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "trigger", name="uq_procedure_user_trigger"),)


class Episode(Base):
    __tablename__ = "episode"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("memory_user.user_id", ondelete="CASCADE"))
    summary: Mapped[str] = mapped_column(Text)
    chroma_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MemoryAudit(Base):
    __tablename__ = "memory_audit"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("memory_user.user_id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String)
    target: Mapped[str] = mapped_column(String)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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
        elif ":memory:" in url:
            # `write(background=True)` fait tourner l'extraction sur un thread
            # du pool (cf. memory/__init__.py) : sans StaticPool, chaque thread
            # obtiendrait sa propre base `:memory:` isolée (comportement par
            # défaut de SQLAlchemy pour ce DSN) — les écritures de fond
            # deviendraient invisibles au thread appelant. Sans objet pour
            # Postgres/SQLite fichier (une vraie base partagée, pas un artefact
            # par connexion).
            engine = create_engine(
                url, future=True, poolclass=StaticPool, connect_args={"check_same_thread": False}
            )
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


def _norm(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn"
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_or_create_user(session: Session, user_id: str) -> MemoryUser:
    user = session.get(MemoryUser, user_id)
    if user is None:
        user = MemoryUser(user_id=user_id, created_at=utcnow())
        session.add(user)
        session.commit()
    return user


def get_or_create_active_thread(
    session: Session, user_id: str, session_gap_hours: float, now: datetime | None = None
) -> Conversation:
    now = now or utcnow()
    gap = timedelta(hours=session_gap_hours)
    latest = session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.last_message_at.desc())
    ).first()
    if latest is not None and (now - latest.last_message_at) <= gap:
        return latest
    thread = Conversation(
        thread_id=new_id("th"),
        user_id=user_id,
        summary="",
        token_count=0,
        summarized_up_to_turn=0,
        started_at=now,
        last_message_at=now,
    )
    session.add(thread)
    session.commit()
    return thread


def _next_turn(session: Session, thread_id: str) -> int:
    last = session.scalars(
        select(Message.turn).where(Message.thread_id == thread_id).order_by(Message.turn.desc())
    ).first()
    return (last or 0) + 1


def append_message(
    session: Session,
    thread_id: str,
    user_id: str,
    role: str,
    content: str,
    now: datetime | None = None,
) -> Message:
    conv = session.get(Conversation, thread_id)
    assert conv is not None
    turn = _next_turn(session, thread_id)
    msg = Message(
        id=new_id("msg"),
        thread_id=thread_id,
        user_id=user_id,
        role=role,
        content=content,
        turn=turn,
    )
    session.add(msg)
    conv.token_count += max(1, len(content) // 4)
    conv.last_message_at = now or utcnow()
    return msg


def recent_messages(session: Session, thread_id: str, limit: int | None) -> list[Message]:
    query = select(Message).where(Message.thread_id == thread_id).order_by(Message.turn.desc())
    if limit is not None:
        query = query.limit(limit)
    rows = session.scalars(query).all()
    return list(reversed(rows))


def older_messages(
    session: Session, thread_id: str, keep_last_n_messages: int, summarized_up_to_turn: int
) -> list[Message]:
    max_turn = (
        session.scalars(
            select(Message.turn).where(Message.thread_id == thread_id).order_by(Message.turn.desc())
        ).first()
        or 0
    )
    cutoff = max_turn - keep_last_n_messages
    if cutoff <= summarized_up_to_turn:
        return []
    rows = session.scalars(
        select(Message)
        .where(
            Message.thread_id == thread_id,
            Message.turn > summarized_up_to_turn,
            Message.turn <= cutoff,
        )
        .order_by(Message.turn.asc())
    ).all()
    return list(rows)


def upsert_fact(
    session: Session,
    user_id: str,
    key: str,
    value: str,
    type_: str,
    confidence: float,
    source_thread_id: str | None,
) -> tuple[Fact, bool]:
    existing = session.scalars(select(Fact).where(Fact.user_id == user_id, Fact.key == key)).first()
    now = utcnow()
    if existing is None:
        fact = Fact(
            id=new_id("fact"),
            user_id=user_id,
            key=key,
            value=value,
            type=type_,
            confidence=confidence,
            source_thread_id=source_thread_id,
            created_at=now,
            updated_at=now,
        )
        session.add(fact)
        return fact, True
    if existing.value == value:
        return existing, False
    existing.value = value
    existing.confidence = confidence
    existing.source_thread_id = source_thread_id
    existing.updated_at = now
    return existing, True


FACT_KEY_ALIASES = {
    "adresse": "address",
    "adresse de livraison": "address",
    "commande": "order_number",
    "numero": "order_number",
    "numero de commande": "order_number",
    "taille": "shoe_size",
    "pointure": "shoe_size",
    "club": "clubs",
    "clubs": "clubs",
    "contrat": "contract_number",
    "tutoiement": "address_mode",
}


def delete_facts_matching(session: Session, user_id: str, target: str) -> list[Fact]:
    key = FACT_KEY_ALIASES.get(_norm(target))
    if key:
        matches = session.scalars(
            select(Fact).where(Fact.user_id == user_id, Fact.key == key)
        ).all()
    else:
        pattern = f"%{_escape_like(target)}%"
        matches = session.scalars(
            select(Fact).where(Fact.user_id == user_id, Fact.key.ilike(pattern, escape="\\"))
        ).all()
    for fact in matches:
        session.delete(fact)
    return list(matches)


def redact_messages(session: Session, user_id: str, value: str) -> int:
    pattern = f"%{_escape_like(value)}%"
    rows = session.scalars(
        select(Message).where(
            Message.user_id == user_id, Message.content.ilike(pattern, escape="\\")
        )
    ).all()
    for msg in rows:
        msg.content = msg.content.replace(value, "[information supprimée]")
    return len(rows)


def upsert_procedure(
    session: Session,
    user_id: str,
    trigger: str,
    rule: str,
    confidence: float,
    source_thread_id: str | None,
) -> tuple[Procedure, bool]:
    """Insérer ou mettre à jour une procédure.

    Retourne (procédure, booléen) où le booléen indique si la procédure a changé.
    """
    existing = session.scalars(
        select(Procedure).where(
            Procedure.user_id == user_id, Procedure.trigger == trigger
        )
    ).first()
    now = utcnow()
    if existing is None:
        proc = Procedure(
            id=new_id("proc"),
            user_id=user_id,
            trigger=trigger,
            rule=rule,
            confidence=confidence,
            active=True,
            source_thread_id=source_thread_id,
            created_at=now,
            updated_at=now,
        )
        session.add(proc)
        return proc, True
    if existing.rule == rule:
        return existing, False
    existing.rule = rule
    existing.confidence = confidence
    existing.source_thread_id = source_thread_id
    existing.updated_at = now
    return existing, True


def delete_procedure_matching(
    session: Session, user_id: str, target: str
) -> list[Procedure]:
    """Supprimer les procédures dont le trigger ou la règle contient target.

    Retourne la liste des procédures supprimées.
    """
    pattern = f"%{_escape_like(target)}%"
    matches = session.scalars(
        select(Procedure).where(
            Procedure.user_id == user_id,
            or_(
                Procedure.trigger.ilike(pattern, escape="\\"),
                Procedure.rule.ilike(pattern, escape="\\"),
            ),
        )
    ).all()
    for proc in matches:
        session.delete(proc)
    return list(matches)


def add_episode(
    session: Session,
    user_id: str,
    summary: str,
    source_thread_id: str | None,
    chroma_id: str | None = None,
) -> Episode:
    episode = Episode(
        id=new_id("epi"),
        user_id=user_id,
        summary=summary,
        chroma_id=chroma_id,
        source_thread_id=source_thread_id,
        occurred_at=utcnow(),
    )
    session.add(episode)
    return episode


def delete_episodes_matching(session: Session, user_id: str, value: str) -> list[Episode]:
    pattern = f"%{_escape_like(value)}%"
    matches = session.scalars(
        select(Episode).where(
            Episode.user_id == user_id, Episode.summary.ilike(pattern, escape="\\")
        )
    ).all()
    for episode in matches:
        session.delete(episode)
    return list(matches)


def write_audit(session: Session, user_id: str, action: str, target: str) -> None:
    session.add(
        MemoryAudit(id=new_id("aud"), user_id=user_id, action=action, target=target, at=utcnow())
    )


def list_facts(session: Session, user_id: str) -> list[Fact]:
    return list(session.scalars(select(Fact).where(Fact.user_id == user_id)).all())


def list_procedures(session: Session, user_id: str) -> list[Procedure]:
    return list(session.scalars(select(Procedure).where(Procedure.user_id == user_id)).all())


def list_episodes(session: Session, user_id: str) -> list[Episode]:
    return list(session.scalars(select(Episode).where(Episode.user_id == user_id)).all())


def list_recent_audit(session: Session, user_id: str, limit: int = 50) -> list[MemoryAudit]:
    return list(
        session.scalars(
            select(MemoryAudit)
            .where(MemoryAudit.user_id == user_id)
            .order_by(MemoryAudit.at.desc())
            .limit(limit)
        ).all()
    )
