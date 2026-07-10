from __future__ import annotations

from datetime import datetime, timedelta

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
    add_episode,
    append_message,
    delete_episodes_matching,
    delete_facts_matching,
    delete_procedure_matching,
    get_or_create_active_thread,
    get_or_create_user,
    list_episodes,
    list_facts,
    list_procedures,
    list_recent_audit,
    make_memory_engine,
    new_id,
    older_messages,
    recent_messages,
    redact_messages,
    upsert_fact,
    upsert_procedure,
    write_audit,
)


def test_new_id_has_prefix():
    assert new_id("fact").startswith("fact-")
    assert len(new_id("fact")) == len("fact-") + 8


def test_unreachable_postgres_falls_back_to_sqlite():
    with pytest.warns(RuntimeWarning, match="injoignable"):
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
    session.add(
        Fact(
            id=new_id("fact"),
            user_id="u1",
            key="shoe_size",
            value="L",
            type="preference",
            confidence=0.9,
        )
    )
    session.commit()
    session.add(
        Fact(
            id=new_id("fact"),
            user_id="u1",
            key="shoe_size",
            value="M",
            type="preference",
            confidence=0.9,
        )
    )
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
    session.add(
        Conversation(
            thread_id=thread_id, user_id="u2", summary="", token_count=0, summarized_up_to_turn=0
        )
    )
    session.add(
        Message(
            id=new_id("msg"), thread_id=thread_id, user_id="u2", role="user", content="hi", turn=1
        )
    )
    session.add(
        Fact(id=new_id("fact"), user_id="u2", key="k", value="v", type="preference", confidence=0.9)
    )
    session.add(MemoryAudit(id=new_id("aud"), user_id="u2", action="write", target="k"))
    session.commit()

    user = session.get(MemoryUser, "u2")
    session.delete(user)
    session.commit()

    assert session.scalars(select(Message).where(Message.user_id == "u2")).all() == []
    assert session.scalars(select(Fact).where(Fact.user_id == "u2")).all() == []
    assert session.scalars(select(MemoryAudit).where(MemoryAudit.user_id == "u2")).all() == []
    session.close()


def _session():
    engine = make_memory_engine("sqlite:///:memory:")
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def test_thread_reused_within_session_gap_and_rotated_after():
    session = _session()
    get_or_create_user(session, "u3")
    t0 = datetime(2026, 1, 1, 10, 0, 0)
    thread1 = get_or_create_active_thread(session, "u3", session_gap_hours=4, now=t0)
    append_message(session, thread1.thread_id, "u3", "user", "hello", now=t0)
    session.commit()

    thread_same = get_or_create_active_thread(
        session, "u3", session_gap_hours=4, now=t0 + timedelta(hours=1)
    )
    assert thread_same.thread_id == thread1.thread_id

    # Activity at t0+1h must refresh last_message_at (last-activity semantics,
    # not thread-creation-time semantics): a check at t0+4h30 is only 3h30
    # after that activity, so the thread must still be considered fresh even
    # though it is 4h30 after the thread was first opened.
    append_message(
        session, thread_same.thread_id, "u3", "user", "still here", now=t0 + timedelta(hours=1)
    )
    session.commit()
    thread_still_same = get_or_create_active_thread(
        session, "u3", session_gap_hours=4, now=t0 + timedelta(hours=4, minutes=30)
    )
    assert thread_still_same.thread_id == thread1.thread_id

    thread_new = get_or_create_active_thread(
        session, "u3", session_gap_hours=4, now=t0 + timedelta(hours=1) + timedelta(hours=5)
    )
    assert thread_new.thread_id != thread1.thread_id
    session.close()


def test_append_message_increments_turn_and_token_count():
    session = _session()
    get_or_create_user(session, "u4")
    thread = get_or_create_active_thread(session, "u4", session_gap_hours=4)
    m1 = append_message(session, thread.thread_id, "u4", "user", "Bonjour")
    m2 = append_message(session, thread.thread_id, "u4", "assistant", "Bonjour !")
    session.commit()
    assert m1.turn == 1
    assert m2.turn == 2
    assert thread.token_count > 0
    session.close()


def test_recent_messages_all_vs_limited():
    session = _session()
    get_or_create_user(session, "u5")
    thread = get_or_create_active_thread(session, "u5", session_gap_hours=4)
    for i in range(6):
        append_message(session, thread.thread_id, "u5", "user", f"msg{i}")
    session.commit()
    assert len(recent_messages(session, thread.thread_id, None)) == 6
    last_two = recent_messages(session, thread.thread_id, 2)
    assert [m.content for m in last_two] == ["msg4", "msg5"]
    session.close()


def test_older_messages_respects_summarized_cursor():
    session = _session()
    get_or_create_user(session, "u6")
    thread = get_or_create_active_thread(session, "u6", session_gap_hours=4)
    for i in range(6):
        append_message(session, thread.thread_id, "u6", "user", f"msg{i}")
    session.commit()
    older = older_messages(
        session, thread.thread_id, keep_last_n_messages=2, summarized_up_to_turn=0
    )
    assert [m.turn for m in older] == [1, 2, 3, 4]
    older_again = older_messages(
        session, thread.thread_id, keep_last_n_messages=2, summarized_up_to_turn=4
    )
    assert older_again == []
    session.close()


def test_upsert_fact_dedups_and_reports_change():
    session = _session()
    get_or_create_user(session, "u7")
    fact, changed = upsert_fact(session, "u7", "shoe_size", "L", "preference", 0.9, None)
    session.commit()
    assert changed is True
    fact2, changed2 = upsert_fact(session, "u7", "shoe_size", "L", "preference", 0.9, None)
    session.commit()
    assert changed2 is False
    assert fact2.id == fact.id
    fact3, changed3 = upsert_fact(session, "u7", "shoe_size", "M", "preference", 0.9, None)
    session.commit()
    assert changed3 is True
    assert fact3.id == fact.id
    assert len(list_facts(session, "u7")) == 1
    session.close()


def test_delete_facts_matching_by_alias_and_redact_messages():
    session = _session()
    get_or_create_user(session, "u8")
    thread = get_or_create_active_thread(session, "u8", session_gap_hours=4)
    append_message(
        session, thread.thread_id, "u8", "user", "Mon adresse de livraison est 12 rue des Lilas."
    )
    session.commit()
    upsert_fact(session, "u8", "address", "12 rue des Lilas", "identity", 0.85, thread.thread_id)
    session.commit()

    removed = delete_facts_matching(session, "u8", "adresse")
    assert len(removed) == 1
    session.commit()
    assert list_facts(session, "u8") == []

    redacted = redact_messages(session, "u8", "12 rue des Lilas")
    session.commit()
    assert redacted == 1
    msg = recent_messages(session, thread.thread_id, None)[0]
    assert "rue des Lilas" not in msg.content
    session.close()


def test_episode_add_list_delete_and_audit_log():
    session = _session()
    get_or_create_user(session, "u9")
    episode = add_episode(session, "u9", "Litige signalé sur commande X", None)
    session.commit()
    assert episode.summary == "Litige signalé sur commande X"
    assert len(list_episodes(session, "u9")) == 1

    matches = delete_episodes_matching(session, "u9", "Litige")
    session.commit()
    assert len(matches) == 1
    assert list_episodes(session, "u9") == []

    write_audit(session, "u9", "delete", "episode")
    session.commit()
    audit = list_recent_audit(session, "u9")
    assert audit[0].action == "delete"
    session.close()


def test_list_procedures_empty_by_default():
    session = _session()
    get_or_create_user(session, "u10")
    assert list_procedures(session, "u10") == []
    session.close()


def test_upsert_procedure_inserts_then_updates():
    session = _session()
    get_or_create_user(session, "p1")

    proc, changed = upsert_procedure(session, "p1", "refund_offer", "Bon de 10%.", 0.8, "th-x")
    session.commit()
    assert changed is True
    assert proc.trigger == "refund_offer"

    _, changed_same = upsert_procedure(session, "p1", "refund_offer", "Bon de 10%.", 0.8, "th-x")
    assert changed_same is False  # règle identique = pas de changement

    updated, changed_new = upsert_procedure(
        session, "p1", "refund_offer", "Bon de 20%.", 0.9, "th-y"
    )
    session.commit()
    assert changed_new is True
    assert updated.rule == "Bon de 20%."


def test_delete_procedure_matching_by_trigger_and_rule():
    session = _session()
    get_or_create_user(session, "p2")
    upsert_procedure(session, "p2", "refund_offer", "Proposer un geste commercial.", 0.9, None)
    upsert_procedure(session, "p2", "greeting", "Tutoyer le client.", 0.9, None)
    session.commit()

    removed = delete_procedure_matching(session, "p2", "refund")
    session.commit()
    assert len(removed) == 1
    assert {p.trigger for p in list_procedures(session, "p2")} == {"greeting"}
    session.close()
