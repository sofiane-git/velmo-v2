"""D3-03 — un store durable est opt-in en dev ou fail-fast en prod, jamais un
repli SQLite/local silencieux."""

from __future__ import annotations

import pytest

from velmo.config import ConfigurationError, Settings, require_durable_store

_UNREACHABLE_PG = "postgresql+psycopg://u:p@127.0.0.1:1/none"


def _prod(monkeypatch: pytest.MonkeyPatch, *, allow: bool = False) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOW_SQLITE_FALLBACK", "true" if allow else "false")


def test_require_durable_store_raises_in_production(monkeypatch):
    _prod(monkeypatch)
    with pytest.raises(ConfigurationError, match="repli local désactivé"):
        require_durable_store("mémoire", _UNREACHABLE_PG)


def test_require_durable_store_noop_in_development(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    # Ne lève pas : le repli local est la commodité attendue en dev/CI.
    require_durable_store("mémoire", _UNREACHABLE_PG)


def test_require_durable_store_opt_in_allows_production_fallback(monkeypatch):
    _prod(monkeypatch, allow=True)
    require_durable_store("mémoire", _UNREACHABLE_PG)


def test_require_durable_store_accepts_explicit_settings():
    settings = Settings(environment="production", allow_sqlite_fallback=False)
    with pytest.raises(ConfigurationError):
        require_durable_store("checkpoints LangGraph", _UNREACHABLE_PG, settings)


def test_make_guardrails_engine_fails_fast_in_production(monkeypatch):
    # Intégration : le repli SQLite d'un store durable passe par
    # require_durable_store — injoignable + prod = échec au démarrage.
    from velmo.guardrails.db import make_guardrails_engine

    _prod(monkeypatch)
    with pytest.raises(ConfigurationError):
        make_guardrails_engine(_UNREACHABLE_PG)
