"""Jobs de purge RGPD (minimisation) — deux TTL distincts pour deux finalités
distinctes (voir conception_chantier1_memoire.md §Rétention & purge) :
- épisodes (résumé + embedding) : 24 mois, sert la personnalisation (R2).
- threads/checkpoints bruts : 90 jours d'inactivité, sert la conversation en
  cours + une fenêtre de preuve/litige — pas calendaire, sur inactivité.

Ces fonctions sont conçues pour être appelées par un job planifié externe
(cron, tâche Azure planifiée — voir docs/job/tuto_azure_deploiement.md), pas
sur le chemin de requête.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from velmo.memory.db import Episode, Thread, utcnow, write_audit

if TYPE_CHECKING:
    from velmo.memory import MemoryManager


def purge_expired_episodes(session: Session, ttl_days: int = 730) -> int:
    cutoff = utcnow() - timedelta(days=ttl_days)
    expired = session.scalars(select(Episode).where(Episode.occurred_at < cutoff)).all()
    for episode in expired:
        write_audit(session, episode.user_id, "delete", f"episode:{episode.id}", actor="system")
        session.delete(episode)
    return len(expired)


def purge_inactive_threads(manager: "MemoryManager", ttl_days: int = 90) -> int:
    """Purge les threads inactifs depuis `ttl_days` — thread (en-tête) et ses
    checkpoints LangGraph associés (même chemin idempotent que R5)."""
    cutoff = utcnow() - timedelta(days=ttl_days)
    session = manager._Session()
    try:
        stale = session.scalars(select(Thread).where(Thread.last_message_at < cutoff)).all()
        thread_ids = [t.thread_id for t in stale]
        for thread in stale:
            write_audit(
                session, thread.user_id, "delete", f"thread:{thread.thread_id}", actor="system"
            )
            session.delete(thread)
        session.commit()
    finally:
        session.close()

    for thread_id in thread_ids:
        # `manager._checkpointer` est la référence gardée explicitement par
        # `MemoryManager.__init__` (Task 4) — plus sûr que de supposer un
        # attribut `.checkpointer` sur l'objet graphe compilé. Vérifié
        # empiriquement (Task 7) : `delete_thread(thread_id: str) -> None`
        # existe bien sur `BaseCheckpointSaver`/`SqliteSaver`/`PostgresSaver`
        # dans les versions installées (langgraph-checkpoint-{sqlite,postgres}
        # 3.1.x) et supprime les lignes `checkpoints`/`writes` (ou
        # `checkpoint_blobs`/`checkpoint_writes` côté Postgres) pour ce thread.
        manager._checkpointer.delete_thread(thread_id)

    return len(thread_ids)
