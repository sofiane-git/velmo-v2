from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from velmo.memory.db import (
    Conversation,
    Fact,
    MemoryAudit,
    MemoryUser,
    Message,
    make_memory_engine,
    new_id,
)


def test_new_id_has_prefix():
    assert new_id("fact").startswith("fact-")
    assert len(new_id("fact")) == len("fact-") + 8


def test_unreachable_postgres_falls_back_to_sqlite():
    engine = make_memory_engine("postgresql+psycopg://x:x@127.0.0.1:1/doesnotexist")
    assert engine.url.drivername == "sqlite"


def test_explicit_url_used_verbatim():
    engine = make_memory_engine("sqlite:///:memory:")
    assert engine.url.drivername == "sqlite"


def test_tables_created_and_fact_unique_per_user_key():
    engine = make_memory_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = Session()
    session.add(MemoryUser(user_id="u1"))
    session.commit()
    session.add(Fact(id=new_id("fact"), user_id="u1", key="shoe_size", value="L", type="preference", confidence=0.9))
    session.commit()
    session.add(Fact(id=new_id("fact"), user_id="u1", key="shoe_size", value="M", type="preference", confidence=0.9))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.close()


def test_cascade_delete_removes_children_on_sqlite():
    engine = make_memory_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = Session()
    session.add(MemoryUser(user_id="u2"))
    session.commit()
    thread_id = new_id("th")
    session.add(Conversation(thread_id=thread_id, user_id="u2", summary="", token_count=0, summarized_up_to_turn=0))
    session.add(Message(id=new_id("msg"), thread_id=thread_id, user_id="u2", role="user", content="hi", turn=1))
    session.add(Fact(id=new_id("fact"), user_id="u2", key="k", value="v", type="preference", confidence=0.9))
    session.add(MemoryAudit(id=new_id("aud"), user_id="u2", action="write", target="k"))
    session.commit()

    user = session.get(MemoryUser, "u2")
    session.delete(user)
    session.commit()

    assert session.scalars(select(Message).where(Message.user_id == "u2")).all() == []
    assert session.scalars(select(Fact).where(Fact.user_id == "u2")).all() == []
    assert session.scalars(select(MemoryAudit).where(MemoryAudit.user_id == "u2")).all() == []
    session.close()
