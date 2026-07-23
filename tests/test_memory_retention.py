from __future__ import annotations

from datetime import timedelta

from velmo.memory import MemoryManager
from velmo.memory.db import list_episodes, utcnow
from velmo.memory.retention import purge_expired_episodes, purge_inactive_threads


def test_purge_expired_episodes_removes_old_rows() -> None:
    mm = MemoryManager(db_url="sqlite:///:memory:")
    session = mm._Session()
    try:
        mm._bind_user(session, "acc-purge")
        from velmo.memory.db import get_or_create_user, add_episode

        get_or_create_user(session, "acc-purge")
        old_episode = add_episode(session, "acc-purge", "Vieux litige", None)
        old_episode.occurred_at = utcnow() - timedelta(days=800)
        add_episode(session, "acc-purge", "Litige récent", None)
        session.commit()

        removed = purge_expired_episodes(session, ttl_days=730)
        session.commit()

        assert removed == 1
        remaining = [e.summary for e in list_episodes(session, "acc-purge")]
        assert remaining == ["Litige récent"]
    finally:
        session.close()


def test_purge_inactive_threads_removes_old_threads() -> None:
    mm = MemoryManager(db_url="sqlite:///:memory:")
    session = mm._Session()
    try:
        from velmo.memory.db import get_or_create_active_thread, get_or_create_user

        get_or_create_user(session, "acc-thread-purge")
        thread = get_or_create_active_thread(session, "acc-thread-purge", 4.0)
        thread.last_message_at = utcnow() - timedelta(days=100)
        session.commit()
    finally:
        session.close()

    removed = purge_inactive_threads(mm, ttl_days=90)
    assert removed == 1
