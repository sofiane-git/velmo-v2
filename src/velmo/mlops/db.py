"""Schéma de persistance MLOps : `agent_version` (identité d'une version),
`eval_run` (résultat agrégé d'une exécution), `eval_case_result` (détail par
cas). Append-only par convention applicative (voir §Robustesse du plan) — la
restriction stricte au niveau rôle Postgres (INSERT/SELECT seuls) est une
tâche d'exploitation documentée dans `docs/job/tuto_azure_deploiement.md`,
pas quelque chose qu'une migration Alembic peut imposer sur un rôle qui
n'existe pas encore dans ce projet à connexion unique.

Résolution d'engine : même convention que `memory/db.py`/`guardrails/db.py`
(Postgres réel si joignable, sinon repli SQLite fichier).
"""

from __future__ import annotations

import sqlite3
import warnings
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    Engine,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AgentVersion(Base):
    __tablename__ = "agent_version"
    version_tag: Mapped[str] = mapped_column(String, primary_key=True)
    prompt_hash: Mapped[str] = mapped_column(String)
    memory_config_hash: Mapped[str] = mapped_column(String)
    guardrail_config_hash: Mapped[str] = mapped_column(String)
    # Seuils de gate hashés (Settings.gate_*, audit D8-05) — nullable : les
    # versions enregistrées avant la migration 0011 n'en ont pas.
    gate_config_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    git_commit: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EvalRun(Base):
    __tablename__ = "eval_run"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    version_tag: Mapped[str] = mapped_column(ForeignKey("agent_version.version_tag"))
    note_memory: Mapped[float] = mapped_column(Float)
    note_guardrails: Mapped[float] = mapped_column(Float)
    note_quality: Mapped[float] = mapped_column(Float)
    note_globale: Mapped[float] = mapped_column(Float)
    global_gate: Mapped[float] = mapped_column(Float)
    gate_passed: Mapped[bool] = mapped_column(Boolean)
    block_rate: Mapped[float] = mapped_column(Float)
    false_positive_rate: Mapped[float] = mapped_column(Float)
    latency_p50_ms: Mapped[float] = mapped_column(Float)
    latency_p95_ms: Mapped[float] = mapped_column(Float)
    cost_per_conv: Mapped[float] = mapped_column(Float)
    langfuse_trace_url: Mapped[str | None] = mapped_column(String, nullable=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    triggered_by: Mapped[str] = mapped_column(String, default="manual")  # ci|manual|nightly|hotfix


class EvalCaseResult(Base):
    __tablename__ = "eval_case_result"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("eval_run.id"))
    case_id: Mapped[str] = mapped_column(String)
    suite: Mapped[str] = mapped_column(String)  # memory|guardrails|quality
    passed: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[float] = mapped_column(Float)
    retried: Mapped[bool] = mapped_column(Boolean, default=False)
    error_kind: Mapped[str | None] = mapped_column(String, nullable=True)  # infra|agent|None


class DriftCheckRun(Base):
    """Mesure d'un run de drift ciblé (hors gate `EvalRun`, dont les 3 notes
    sont NOT NULL — un run partiel ne peut pas s'y persister). Historique
    requis par la règle « deux nuits consécutives » (conception ch.3
    §Rollback, audit D8-03)."""

    __tablename__ = "drift_check_run"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    suite: Mapped[str] = mapped_column(String)  # memory|guardrails|quality
    cases: Mapped[int] = mapped_column(Integer)
    passed: Mapped[int] = mapped_column(Integer)
    note: Mapped[float] = mapped_column(Float)
    ran_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    triggered_by: Mapped[str] = mapped_column(String, default="model-drift")


def _default_sqlite_path() -> Path:
    return Path(__file__).resolve().parents[3] / "var" / "velmo_mlops.db"


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


def make_mlops_engine(url: str | None = None) -> Engine:
    """Postgres réel si `url` (ou `DB_URL`) est joignable ; sinon SQLite fichier
    persistant. Même convention que `memory/db.py::make_memory_engine`.

    `url=None` résout `DB_URL` via `get_settings().db_url` puis applique la
    même logique que `url` explicite (au lieu de ne considérer que le cas
    Postgres) : une `DB_URL` déjà SQLite (ex. fichier temporaire de test) est
    utilisée telle quelle, pas silencieusement écrasée par le repli par
    défaut — sinon `GET /mlops/gate/history` ne verrait jamais les lignes
    qu'un test vient de persister via `DB_URL=sqlite:///...`.
    """
    from velmo.config import get_settings, require_durable_store

    if url is None:
        url = get_settings().db_url

    if url.startswith("postgresql") and not _postgres_reachable(url):
        require_durable_store("MLOps", url)
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
