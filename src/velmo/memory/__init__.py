"""Mémoire de l'agent Velmo : contexte court terme et mémoire long terme,
isolée par utilisateur. Orchestration de trois briques indépendantes :
`memory.db` (persistance relationnelle), `memory.extractor` (décision
d'écriture long terme), `memory.episodic` (rappel par similarité). La mémoire
n'est jamais exposée comme outil au LLM — elle encadre l'appel LLM côté
`Agent` (lecture avant, écriture après).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from .db import (
    Conversation,
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
    make_memory_engine,
    older_messages,
    recent_messages,
    redact_messages,
    upsert_fact,
    upsert_procedure,
    write_audit,
)
from velmo.llm import LLM, get_llm

from .episodic import EpisodicStore, get_episodic_backend
from .extractor import FactExtractor, RuleBasedExtractor

Turn = tuple[str, str]

SUMMARY_SYSTEM = (
    "Résume l'historique de conversation en 2-3 phrases maximum. "
    "Préserve tous les chiffres, numéros de commande, litiges et engagements. "
    "Ne résume pas les entités nommées."
)


@dataclass
class MemoryContext:
    """Contexte mémoire restitué pour une requête utilisateur."""

    history: list[Turn] = field(default_factory=list)
    facts: dict[str, str] = field(default_factory=dict)
    episodic: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Sérialise le contexte en texte (injectable dans un prompt)."""
        parts: list[str] = []
        for role, content in self.history:
            parts.append(f"{role}: {content}")
        for key, value in self.facts.items():
            parts.append(f"fact:{key}={value}")
        parts.extend(self.episodic)
        return "\n".join(parts)


class MemoryManager:
    """Orchestre la mémoire court terme et long terme, isolée par utilisateur."""

    def __init__(
        self,
        *,
        token_budget: int = 2000,
        confidence_threshold: float = 0.7,
        session_gap_hours: float = 4.0,
        keep_last_n_turns: int = 10,
        db_url: str | None = None,
        extractor: FactExtractor | None = None,
        episodic_store: EpisodicStore | None = None,
        llm: LLM | None = None,
    ) -> None:
        self.token_budget = token_budget
        self.confidence_threshold = confidence_threshold
        self.session_gap_hours = session_gap_hours
        self.keep_last_n_turns = keep_last_n_turns
        engine = make_memory_engine(db_url)
        self._Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        self.extractor = extractor or RuleBasedExtractor()
        self.episodic_store = episodic_store or get_episodic_backend()
        self.llm = llm or get_llm()

    def _bind_user(self, session: Session, user_id: str) -> None:
        """Positionne le GUC PostgreSQL consommé par les policies RLS.

        `set_config(..., is_local=true)` reste limité à la transaction courante.
        No-op hors Postgres (SQLite de test) : les policies RLS n'existent pas.
        """
        if session.get_bind().dialect.name != "postgresql":
            return
        session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": user_id}
        )

    def read(self, user_id: str, message: str) -> MemoryContext:
        session = self._Session()
        try:
            self._bind_user(session, user_id)
            get_or_create_user(session, user_id)
            thread = get_or_create_active_thread(session, user_id, self.session_gap_hours)
            history: list[Turn] = []
            if thread.summary:
                history.append(("résumé", thread.summary))
            limit = None if thread.token_count <= self.token_budget else self.keep_last_n_turns * 2
            for msg in recent_messages(session, thread.thread_id, limit):
                history.append((msg.role, msg.content))
            facts = {f.key: f.value for f in list_facts(session, user_id)}
            episodic = self.episodic_store.search(user_id, message, k=3)
            return MemoryContext(history=history, facts=facts, episodic=episodic)
        finally:
            session.close()

    def write(self, user_id: str, user_message: str, assistant_message: str) -> None:
        session = self._Session()
        try:
            self._bind_user(session, user_id)
            get_or_create_user(session, user_id)
            thread = get_or_create_active_thread(session, user_id, self.session_gap_hours)
            append_message(session, thread.thread_id, user_id, "user", user_message)
            append_message(session, thread.thread_id, user_id, "assistant", assistant_message)
            session.commit()

            extracted = self.extractor.extract(user_message, assistant_message)
            has_dispute = False
            for ef in extracted.facts:
                if ef.confidence < self.confidence_threshold:
                    continue
                _, changed = upsert_fact(
                    session, user_id, ef.key, ef.value, ef.type, ef.confidence, thread.thread_id
                )
                if changed:
                    write_audit(session, user_id, "write", f"fact:{ef.key}")
                if ef.type == "dispute":
                    has_dispute = True

            for ep in extracted.procedures:
                if ep.confidence < self.confidence_threshold:
                    continue
                _, changed = upsert_procedure(
                    session, user_id, ep.trigger, ep.rule, ep.confidence, thread.thread_id
                )
                if changed:
                    write_audit(session, user_id, "write", f"procedure:{ep.trigger}")
            session.commit()

            if has_dispute:
                summary = f"Litige signalé : {user_message.strip()}"
                episode = add_episode(session, user_id, summary, thread.thread_id)
                episode.chroma_id = self.episodic_store.add(user_id, summary, thread.thread_id)
                session.commit()

            self._maybe_compress(session, user_id, thread)
        finally:
            session.close()

    def _maybe_compress(self, session: Session, user_id: str, thread: Conversation) -> None:
        if thread.token_count <= self.token_budget:
            return
        older = older_messages(
            session, thread.thread_id, self.keep_last_n_turns * 2, thread.summarized_up_to_turn
        )
        if not older:
            return
        block = "\n".join(f"{m.role}: {m.content}" for m in older)

        # 1. Extraction préalable : l'info critique quitte le texte volatil avant résumé.
        extracted = self.extractor.extract(block, "")
        for ef in extracted.facts:
            if ef.confidence < self.confidence_threshold:
                continue
            # La capture "dispute" est tour-par-tour (message unique), pas bloc :
            # la ré-extraire sur `block` (concaténation multi-tours) corromprait la
            # valeur déjà capturée correctement à l'écriture.
            if ef.type == "dispute":
                continue
            _, changed = upsert_fact(
                session, user_id, ef.key, ef.value, ef.type, ef.confidence, thread.thread_id
            )
            if changed:
                write_audit(session, user_id, "write", f"fact:{ef.key}")
        for ep in extracted.procedures:
            if ep.confidence < self.confidence_threshold:
                continue
            _, changed = upsert_procedure(
                session, user_id, ep.trigger, ep.rule, ep.confidence, thread.thread_id
            )
            if changed:
                write_audit(session, user_id, "write", f"procedure:{ep.trigger}")

        # 2. Résumé LLM (remplace la concaténation brute).
        summary = self.llm.invoke(SUMMARY_SYSTEM, "", block).strip()

        # 3. Persistance.
        thread.summary = (thread.summary + " " if thread.summary else "") + summary
        thread.summarized_up_to_turn = older[-1].turn
        episode = add_episode(session, user_id, summary, thread.thread_id)
        episode.chroma_id = self.episodic_store.add(user_id, summary, thread.thread_id)
        session.commit()

    def remember_fact(self, user_id: str, key: str, value: str) -> None:
        session = self._Session()
        try:
            self._bind_user(session, user_id)
            get_or_create_user(session, user_id)
            upsert_fact(session, user_id, key, value, "identity", 1.0, None)
            write_audit(session, user_id, "write", f"fact:{key}")
            session.commit()
        finally:
            session.close()

    def forget(self, user_id: str, target: str) -> int:
        session = self._Session()
        try:
            self._bind_user(session, user_id)
            removed_facts = delete_facts_matching(session, user_id, target)
            count = len(removed_facts)
            for fact in removed_facts:
                redact_messages(session, user_id, fact.value)
                removed_episodes = delete_episodes_matching(session, user_id, fact.value)
                count += len(removed_episodes)
                for episode in removed_episodes:
                    if episode.chroma_id:
                        self.episodic_store.delete(episode.chroma_id)
            removed_procs = delete_procedure_matching(session, user_id, target)
            count += len(removed_procs)
            write_audit(session, user_id, "delete", target)
            session.commit()
            return count
        finally:
            session.close()

    def inspect(self, user_id: str) -> dict[str, object]:
        session = self._Session()
        try:
            self._bind_user(session, user_id)
            return {
                "facts": [
                    {
                        "key": f.key,
                        "value": f.value,
                        "type": f.type,
                        "confidence": f.confidence,
                        "source_thread_id": f.source_thread_id,
                        "created_at": f.created_at.isoformat(),
                        "updated_at": f.updated_at.isoformat(),
                    }
                    for f in list_facts(session, user_id)
                ],
                "procedures": [
                    {
                        "trigger": p.trigger,
                        "rule": p.rule,
                        "active": p.active,
                        "confidence": p.confidence,
                        "source_thread_id": p.source_thread_id,
                        "created_at": p.created_at.isoformat(),
                        "updated_at": p.updated_at.isoformat(),
                    }
                    for p in list_procedures(session, user_id)
                ],
                "episodic": [
                    {
                        "summary": e.summary,
                        "source_thread_id": e.source_thread_id,
                        "occurred_at": e.occurred_at.isoformat(),
                    }
                    for e in list_episodes(session, user_id)
                ],
            }
        finally:
            session.close()
