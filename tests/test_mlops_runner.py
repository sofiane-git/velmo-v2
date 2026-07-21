from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from velmo.db import Order, make_engine
from velmo.mlops.observability import NullSink
from velmo.mlops.runner import build_gate_agent


def test_build_gate_agent_returns_working_evaluable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path}/gate_agent.db")
    agent = build_gate_agent(NullSink())
    answer = agent.respond("C-marc-dubois", "Où en est ma commande O-2024-0101 ?")
    assert "O-2024-0101" in answer


def test_build_gate_agent_seeds_reference_data_once_not_twice(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path}/gate_agent_seed.db")
    build_gate_agent(NullSink())
    build_gate_agent(NullSink())

    engine = make_engine()
    session = sessionmaker(bind=engine, future=True)()
    orders = session.execute(select(Order)).all()
    session.close()
    # 14 commandes de référence (src/velmo/sampledata.py::_orders) — un second
    # appel ne doit jamais réinsérer le jeu de données (double seed).
    assert len(orders) == 14
