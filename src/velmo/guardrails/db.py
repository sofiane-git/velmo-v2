"""Schéma du journal de sécurité garde-fous (`guardrail_audit`) et résolution
de l'engine : Postgres réel si joignable (même convention que `memory/db.py`),
sinon repli SQLite fichier. Table et rétention indépendantes de `memory_audit`
(chantier 1) : un log de sécurité peut légitimement survivre à une demande
d'effacement RGPD (R5) pour investigation d'incident — cf.
`conception_chantier2_guardrails.md`.

`user_id` est une clé logique, pas une clé étrangère physique (même
découplage que `memory/db.py` vis-à-vis du schéma métier) : `guardrails` et
`memory` restent deux schemas indépendants, sans dépendance d'ordre
d'écriture entre eux.
"""

from __future__ import annotations

import sqlite3
import uuid
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import DateTime, Engine, Float, String, create_engine, event, func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from velmo.config import get_settings, require_durable_store


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class GuardrailAudit(Base):
    __tablename__ = "guardrail_audit"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String)
    location: Mapped[str] = mapped_column(String)  # "input" | "output"
    method: Mapped[str] = mapped_column(String)  # regex | classifier | llm_judge | ...
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    action: Mapped[str] = mapped_column(String)  # "block" | "flag"
    # Verdict du RuleBasedJudge calculé en shadow mode, format JSON compact
    # (ex. '{"manipulation": 0.0, "hors_role": 0.5}') — NULL si le hit ne vient
    # pas du juge cloud (shadow non applicable) ou si le calcul shadow a échoué.
    shadow_verdict: Mapped[str | None] = mapped_column(String, nullable=True)
    source_thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def _default_sqlite_path() -> Path:
    return Path(__file__).resolve().parents[3] / "var" / "velmo_guardrails.db"


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


def make_guardrails_engine(url: str | None = None) -> Engine:
    """Postgres réel si `url` (ou `DB_URL`) est joignable ; sinon SQLite fichier
    persistant (`var/velmo_guardrails.db`). Même convention que
    `memory/db.py::make_memory_engine` (fichier séparé, jamais `:memory:` par
    défaut hors tests explicites, pour partager l'état entre plusieurs
    `GuardrailEngine()`).

    `url=None` résout `DB_URL` via `get_settings().db_url` puis applique la
    même logique que `url` explicite (au lieu de ne considérer que le cas
    Postgres) : une `DB_URL` déjà SQLite est utilisée telle quelle, pas
    silencieusement écrasée par le repli par défaut.
    """
    if url is None:
        url = get_settings().db_url

    if url.startswith("postgresql") and not _postgres_reachable(url):
        require_durable_store("guardrail_audit", url)
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

        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_connection: object, connection_record: object) -> None:
            if isinstance(dbapi_connection, sqlite3.Connection):
                dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def bind_user(session: Session, user_id: str | None) -> None:
    """Positionne le GUC PostgreSQL consommé par la policy RLS `guardrail_audit`.

    No-op si `user_id` est absent ou hors Postgres (SQLite de test) — même
    mécanisme que `memory/__init__.py::MemoryManager._bind_user`.
    """
    if user_id is None or session.get_bind().dialect.name != "postgresql":
        return
    session.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": user_id})


def write_audit(
    session: Session,
    user_id: str | None,
    category: str,
    location: str,
    method: str,
    score: float | None,
    action: str,
    source_thread_id: str | None,
    shadow_verdict: str | None = None,
) -> GuardrailAudit:
    row = GuardrailAudit(
        id=new_id("gaud"),
        user_id=user_id,
        category=category,
        location=location,
        method=method,
        score=score,
        action=action,
        shadow_verdict=shadow_verdict,
        source_thread_id=source_thread_id,
        created_at=utcnow(),
    )
    session.add(row)
    return row


def count_recent_audit(session: Session, user_id: str, category: str, window: timedelta) -> int:
    since = utcnow() - window
    return (
        session.scalar(
            select(func.count())
            .select_from(GuardrailAudit)
            .where(
                GuardrailAudit.user_id == user_id,
                GuardrailAudit.category == category,
                GuardrailAudit.action.in_(("block", "block_escalate")),
                GuardrailAudit.created_at >= since,
            )
        )
        or 0
    )


def list_recent_audit(session: Session, user_id: str, limit: int = 50) -> list[GuardrailAudit]:
    return list(
        session.scalars(
            select(GuardrailAudit)
            .where(GuardrailAudit.user_id == user_id)
            .order_by(GuardrailAudit.created_at.desc())
            .limit(limit)
        ).all()
    )
