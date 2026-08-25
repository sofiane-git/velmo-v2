"""Mémoire de l'agent Velmo : contexte court terme et mémoire long terme,
isolée par utilisateur. Orchestration de trois briques indépendantes :
`memory.db` (persistance relationnelle), `memory.extractor` (décision
d'écriture long terme), `memory.episodic` (rappel par similarité). La mémoire
n'est jamais exposée comme outil au LLM — elle encadre l'appel LLM côté
`Agent` (lecture avant, écriture après).
"""

from __future__ import annotations

import contextvars
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field

from langchain_core.runnables import RunnableConfig
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from datetime import timedelta

from .db import (
    FACT_KEY_ALIASES as FACT_KEY_ALIASES,
    Thread,
    MemoryUser,
    add_episode,
    delete_episodes_matching,
    delete_facts_matching,
    delete_procedure_matching,
    get_or_create_active_thread,
    get_or_create_user,
    is_tombstoned,
    list_active_tombstones,
    list_episodes,
    list_facts,
    list_procedures,
    list_threads,
    make_memory_engine,
    resolve_tombstone,
    set_tombstone,
    upsert_fact,
    upsert_procedure,
    utcnow,
    write_audit,
)
from velmo.llm import LLM, get_llm
from velmo.config import get_settings
from velmo.guardrails.retrieved import rule_is_wellformed

from .episodic import EpisodicVectorStore, get_episodic_backend
from .extractor import ExtractedFact, ExtractedProcedure, FactExtractor, get_extractor
from .graph import build_graph, get_checkpointer, replace_messages

logger = logging.getLogger(__name__)

Turn = tuple[str, str]


def _rule_is_persistable(trigger: str, rule: str) -> bool:
    """Garde G8 sur l'écriture d'une règle de comportement (menace T5).

    Une `PROCEDURE` est une instruction en langage naturel, produite par un LLM
    depuis du texte utilisateur, puis réinjectée dans le prompt **système** des
    sessions suivantes : c'est une injection de prompt persistante par
    conception, et elle échappe au garde-fou d'entrée du tour où elle agira
    (le contenu hostile n'est alors plus dans le message, il est en base).

    `trigger` était déjà contraint à un vocabulaire fermé côté extracteur ;
    `rule` restait du texte libre non validé — c'est ce trou que ferme ce
    contrôle. **Fail-closed** : au moindre doute on ne persiste pas. L'asymétrie
    des coûts est nette — une écriture refusée est récupérable (l'extraction est
    best-effort, le client peut redonner l'information), une écriture
    malveillante persistée pilote toutes les sessions futures.

    Le contrôle est **local et déterministe** (aucun appel réseau) : il ne peut
    pas tomber, et n'ajoute rien au budget de latence de l'écriture mémoire.
    """
    ok, why = rule_is_wellformed(rule)
    if not ok:
        logger.warning("Écriture mémoire refusée (G8/T5) — trigger=%r : %s", trigger, why)
    return ok


# `render()` sérialise les faits dans le prompt LLM : les clés internes
# (snake_case, ex. `shoe_size`) ne doivent jamais y apparaître telles quelles
# sous peine que le modèle les recopie dans sa réponse au client.
_FACT_KEY_LABELS = {
    "address": "adresse de livraison",
    "order_number": "numéro de commande",
    "shoe_size": "taille",
    "clubs": "club préféré",
    "contract_number": "numéro de contrat",
    "address_mode": "mode de tutoiement",
}

# Extraction long terme (`write(..., background=True)`) et résumé de
# compression (`_maybe_compress`) appellent le même LLM que la génération de
# réponse de l'agent (`velmo.llm.AzureLLM`) — un endpoint lent/indisponible ne
# doit jamais retarder une réponse déjà validée par les garde-fous. Pool dédié
# et petit : ce travail est best-effort (cf. `LLMExtractor.extract`, déjà
# tolérant aux pannes réseau), pas prioritaire face au pool des garde-fous.
# `write(background=False)` (défaut) attend aussi sur ce pool (cf. `write`) :
# 4 workers pour éviter qu'un appel synchrone et un différé ne se disputent
# les 2 seuls threads sous charge concurrente.
_BACKGROUND_EXECUTOR = ThreadPoolExecutor(max_workers=4)

# Attente bornée avant de basculer en `pending` : couvre le cas normal (LLM
# sain, extraction en quelques secondes) sans jamais imposer aux appelants
# l'attente complète du timeout LLM (jusqu'à 45-90s, cf. AzureLLM) — un tour
# suivant qui dépend de ce tour-ci (ex. "oublie ma taille" juste après "ma
# taille est L") reste cohérent tant que l'extraction répond sous ce délai.
_EXTRACTION_WAIT_S = 8.0

SUMMARY_SYSTEM = (
    "Résume l'historique de conversation en 2-3 phrases maximum. "
    "Préserve tous les chiffres, numéros de commande, litiges et engagements. "
    "Ne résume pas les entités nommées.\n\n"
    "Exemple :\n"
    "Historique : client demande le statut de la commande #4471 (maillot Milan AC "
    "1994), agent confirme expédition prévue le 12/03, client précise taille 44 et "
    "signale qu'un précédent colis (commande #4108) est arrivé déchiré, agent "
    "propose un avoir de 15€.\n"
    '→ "Le client (taille 44) suit la commande #4471, expédition prévue le 12/03. '
    "Litige ouvert sur la commande #4108 (colis déchiré), avoir de 15€ proposé par "
    "l'agent.\""
)


@dataclass
class FactRecord:
    key: str
    value: str
    confidence: float


@dataclass
class MemoryContext:
    """Contexte mémoire restitué pour une requête utilisateur."""

    history: list[Turn] = field(default_factory=list)
    facts: dict[str, str] = field(default_factory=dict)
    episodic: list[str] = field(default_factory=list)
    facts_detailed: list[FactRecord] = field(default_factory=list)

    def render(self) -> str:
        """Sérialise le contexte en texte (injectable dans un prompt)."""
        parts: list[str] = []
        for role, content in self.history:
            parts.append(f"{role}: {content}")
        for key, value in self.facts.items():
            label = _FACT_KEY_LABELS.get(key, key)
            parts.append(f"{label} : {value}")
        parts.extend(self.episodic)
        return "\n".join(parts)


@dataclass
class RemovedFact:
    key: str
    value: str


@dataclass
class RemovedProcedure:
    trigger: str
    rule: str


@dataclass
class ForgetReport:
    """Détail de ce qu'une suppression (`forget`/`forget_all`) a réellement
    effacé — la traçabilité (`docs/reference/reco_expert.md`) exige de pouvoir inspecter
    ce qui a été retenu, donc aussi ce qui a été rendu à l'oubli."""

    count: int
    facts: list[RemovedFact] = field(default_factory=list)
    procedures: list[RemovedProcedure] = field(default_factory=list)
    episodes: list[str] = field(default_factory=list)  # résumés


@dataclass
class WriteReport:
    """Ce qui a été classé en mémoire long terme lors d'un `MemoryManager.write()`."""

    facts_written: list[ExtractedFact] = field(default_factory=list)
    procedures_written: list[ExtractedProcedure] = field(default_factory=list)
    episode_created: bool = False
    # True quand `write(..., background=True)` a différé l'extraction LLM :
    # les listes ci-dessus sont vides parce que pas encore calculées, pas
    # parce que rien n'a été trouvé — cf. `write`.
    pending: bool = False


class MemoryManager:
    """Orchestre la mémoire court terme et long terme, isolée par utilisateur."""

    def __init__(
        self,
        *,
        token_budget: int | None = None,
        confidence_threshold: float | None = None,
        session_gap_hours: float = 4.0,
        keep_last_n_turns: int = 10,
        db_url: str | None = None,
        extractor: FactExtractor | None = None,
        episodic_store: EpisodicVectorStore | None = None,
        llm: LLM | None = None,
    ) -> None:
        self.token_budget = (
            token_budget if token_budget is not None else get_settings().memory_token_budget
        )
        self.confidence_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else get_settings().memory_confidence_threshold
        )
        self.session_gap_hours = session_gap_hours
        self.keep_last_n_turns = keep_last_n_turns
        resolved_db_url = db_url or get_settings().db_url
        self.engine = make_memory_engine(db_url)
        self._Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        self._checkpointer_cm = get_checkpointer(resolved_db_url)
        self._checkpointer = self._checkpointer_cm.__enter__()
        self._graph = build_graph(self._checkpointer)
        # `self._checkpointer` (SqliteSaver/PostgresSaver) enveloppe une seule
        # connexion, pas thread-safe : `write()` tourne sur le thread de requête
        # tandis que `_extract_and_persist`/`_maybe_compress`/
        # `_scrub_thread_messages` tournent sur `_BACKGROUND_EXECUTOR` — sans
        # verrou, deux threads peuvent toucher la même connexion en même temps.
        self._graph_lock = threading.Lock()
        self.extractor = extractor or get_extractor()
        self.episodic_store = episodic_store or get_episodic_backend(resolved_db_url)
        self.llm = llm or get_llm()

    def close(self) -> None:
        """Ferme la connexion du checkpointer et le pool de l'engine — à
        appeler à l'arrêt du process (ou implicitement via `__del__` pour les
        instances de test à courte durée de vie, cf. `_BACKGROUND_EXECUTOR`).
        Sans le `dispose()` de l'engine, chaque `MemoryManager` reconstruit
        (ex. un agent frais par cas dans la suite Outils, `mlops/suites/
        tools.py`) laisse son pool de connexions ouvert jusqu'au GC — constaté
        en prod : épuisement des connexions Postgres (`Standard_B1ms`) au
        milieu d'un run réel."""
        self._checkpointer_cm.__exit__(None, None, None)
        self.engine.dispose()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _bind_user(self, session: Session, user_id: str) -> None:
        """Positionne le GUC PostgreSQL consommé par les policies RLS.

        `is_local=false` (`SET`, pas `SET LOCAL`) : le GUC survit à un
        `session.commit()` **tant que la même connexion physique reste
        rattachée à la session**. Insuffisant en soi : un `Session` lié à un
        `Engine` (pool) peut rendre sa connexion au pool après un commit et en
        réacquérir une autre pour la suite, qui n'a jamais reçu ce `SET` —
        constaté en prod deux fois (`thread` lors de la création d'un nouvel
        utilisateur, puis `episode` dans `_maybe_add_episode_guarded`). Tout
        appelant qui écrit après un commit intermédiaire doit rappeler
        `_bind_user` juste avant, pas supposer que le premier appel suffit
        pour le reste de la méthode. Sûr côté isolation même en cas de
        connexion changée : jamais de fuite inter-utilisateur, seulement un
        rejet RLS (fail-closed). No-op hors Postgres (SQLite de test) : les
        policies RLS n'existent pas.
        """
        if session.get_bind().dialect.name != "postgresql":
            return
        session.execute(
            text("SELECT set_config('app.current_user_id', :uid, false)"), {"uid": user_id}
        )

    def _embed_if_postgres(
        self, session: Session, summary: str
    ) -> tuple[list[float] | None, str | None]:
        if session.get_bind().dialect.name != "postgresql":
            return None, None
        from velmo.memory.embeddings import embed_text, embedding_model_id

        return embed_text(summary), embedding_model_id()

    def read(self, user_id: str, message: str) -> MemoryContext:
        session = self._Session()
        try:
            self._bind_user(session, user_id)
            # `get_or_create_user` commit en interne pour un utilisateur
            # nouveau — rebind après (voir docstring de `_bind_user`).
            get_or_create_user(session, user_id)
            self._bind_user(session, user_id)
            thread = get_or_create_active_thread(session, user_id, self.session_gap_hours)
            config: RunnableConfig = {"configurable": {"thread_id": thread.thread_id}}
            with self._graph_lock:
                snapshot = self._graph.get_state(config)
            all_messages = snapshot.values.get("messages", []) if snapshot.values else []
            limit = None if thread.token_count <= self.token_budget else self.keep_last_n_turns * 2
            windowed = all_messages if limit is None else all_messages[-limit:]

            history: list[Turn] = []
            if thread.summary:
                history.append(("résumé", thread.summary))
            history.extend((m["role"], m["content"]) for m in windowed)

            fact_rows = list_facts(session, user_id)
            facts = {f.key: f.value for f in fact_rows}
            facts_detailed = [
                FactRecord(key=f.key, value=f.value, confidence=f.confidence) for f in fact_rows
            ]
            episodic = self.episodic_store.search(session, user_id, message, k=3)
            return MemoryContext(
                history=history, facts=facts, episodic=episodic, facts_detailed=facts_detailed
            )
        finally:
            session.close()

    def write(
        self,
        user_id: str,
        user_message: str,
        assistant_message: str,
        *,
        background: bool = False,
    ) -> WriteReport:
        """Persiste le tour et classe l'extraction long terme.

        `background=True` : la persistance du tour (rapide, local) reste
        synchrone — un tour suivant doit voir cet historique immédiatement
        (cf. `read`). L'extraction (`self.extractor.extract`, potentiellement
        un appel LLM) et tout ce qui en dépend tournent sur
        `_BACKGROUND_EXECUTOR` ; on attend jusqu'à `_EXTRACTION_WAIT_S` avant
        de rendre la main. Cas normal (LLM sain) : le `WriteReport` retourné
        est complet, aucun changement de comportement observable. Cas dégradé
        (LLM lent/indisponible) : au-delà du délai, on rend la main avec
        `pending=True` (listes vides — pas encore calculées, pas "rien
        trouvé") et l'extraction continue en arrière-plan sans jamais bloquer
        la réponse. Callers synchrones existants (tests, usage direct de
        `MemoryManager`) gardent le comportement par défaut inchangé.
        """
        session = self._Session()
        try:
            self._bind_user(session, user_id)
            get_or_create_user(session, user_id)
            self._bind_user(session, user_id)
            thread = get_or_create_active_thread(session, user_id, self.session_gap_hours)
            config: RunnableConfig = {"configurable": {"thread_id": thread.thread_id}}
            with self._graph_lock:
                self._graph.invoke(
                    {
                        "messages": [
                            {"role": "user", "content": user_message},
                            {"role": "assistant", "content": assistant_message},
                        ]
                    },
                    config,
                )
            thread.token_count += max(1, len(user_message) // 4) + max(
                1, len(assistant_message) // 4
            )
            thread.last_message_at = utcnow()
            session.commit()
        finally:
            session.close()

        future = _BACKGROUND_EXECUTOR.submit(
            contextvars.copy_context().run,
            self._extract_and_persist,
            user_id,
            user_message,
            assistant_message,
        )
        try:
            return future.result(timeout=_EXTRACTION_WAIT_S if background else None)
        except FutureTimeoutError:
            return WriteReport(pending=True)

    def _active_value_tombstones_in(self, session: Session, user_id: str, text: str) -> list[str]:
        """Retourne les valeurs `fact_value` sous tombstone actif présentes dans
        `text`, à frontière de mot. Un `in` substring brut sur-bloquerait avec
        des valeurs courtes réalistes ("L", "44") qui matchent à l'intérieur
        d'un mot plus long (ex. "L" dans "Litige signalé", F3) — les lookarounds
        `(?<!\\w)`/`(?!\\w)` évitent ce faux positif tout en restant robustes
        aux valeurs finissant par un caractère non-mot (contrairement à `\\b`).
        Partagé par `_maybe_add_episode_guarded` (garde) et `_maybe_compress`
        (expurgation du résumé, F1) pour matcher exactement les mêmes valeurs."""
        return [
            t.target
            for t in list_active_tombstones(session, user_id)
            if t.target_kind == "fact_value"
            and t.target
            and re.search(rf"(?<!\w){re.escape(t.target)}(?!\w)", text)
        ]

    def _maybe_add_episode_guarded(
        self,
        session: Session,
        user_id: str,
        summary: str,
        thread_id: str | None,
    ) -> bool:
        """Écrit un épisode seulement si son résumé ne reprend aucune valeur
        sous tombstone actif (anti-résurrection étendue aux épisodes, D9-04).
        Retourne True si l'épisode a été écrit."""
        # Rebind défensif : les deux appelants (`_extract_and_persist`,
        # `_maybe_compress`) commitent des écritures intermédiaires avant
        # d'arriver ici — un `commit()` peut rendre la connexion au pool et en
        # réacquérir une autre pour la suite, qui n'a jamais reçu le `SET`
        # `app.current_user_id` (contrairement à ce que documente `_bind_user`,
        # constaté en prod : RLS rejette l'insert `episode`, même bug que le
        # fix `thread` mais pour une connexion changée après commit, pas juste
        # jamais posée).
        self._bind_user(session, user_id)
        if self._active_value_tombstones_in(session, user_id, summary):
            # Pas la valeur elle-même dans le log : elle est censée être oubliée
            # (RGPD) — cf. M1.
            logger.info("épisode écarté pour user_id=%s : valeur sous tombstone", user_id)
            return False
        embedding, model_id = self._embed_if_postgres(session, summary)
        episode = add_episode(session, user_id, summary, thread_id, embedding, model_id)
        self.episodic_store.add(user_id, summary, episode.id)
        return True

    def _extract_and_persist(
        self, user_id: str, user_message: str, assistant_message: str
    ) -> WriteReport:
        session = self._Session()
        try:
            self._bind_user(session, user_id)
            thread = get_or_create_active_thread(session, user_id, self.session_gap_hours)

            extracted = self.extractor.extract(user_message, assistant_message)
            report = WriteReport()
            has_dispute = False
            for ef in extracted.facts:
                if ef.confidence < self.confidence_threshold:
                    continue
                if is_tombstoned(session, user_id, "fact_key", ef.key):
                    continue
                fact, changed = upsert_fact(
                    session, user_id, ef.key, ef.value, ef.type, ef.confidence, thread.thread_id
                )
                if changed:
                    write_audit(session, user_id, "write", f"fact:{fact.key}", actor="extractor")
                report.facts_written.append(ef)
                if ef.type == "dispute":
                    has_dispute = True

            for ep in extracted.procedures:
                if ep.confidence < self.confidence_threshold:
                    continue
                if is_tombstoned(session, user_id, "procedure_trigger", ep.trigger):
                    continue
                if not _rule_is_persistable(ep.trigger, ep.rule):
                    continue
                _, changed = upsert_procedure(
                    session, user_id, ep.trigger, ep.rule, ep.confidence, thread.thread_id
                )
                if changed:
                    write_audit(
                        session, user_id, "write", f"procedure:{ep.trigger}", actor="extractor"
                    )
                report.procedures_written.append(ep)
            session.commit()

            if has_dispute:
                summary = f"Litige signalé : {user_message.strip()}"
                if self._maybe_add_episode_guarded(session, user_id, summary, thread.thread_id):
                    session.commit()
                    report.episode_created = True

            # Note de portée : une éventuelle création d'épisode/fact déclenchée par
            # `_maybe_compress` (résumé de compression, rare en session courte) n'est
            # pas reflétée dans ce rapport — il documente uniquement ce tour-ci.
            self._maybe_compress(session, user_id, thread)
            return report
        except Exception:
            # Ce chemin peut tourner en arrière-plan (`write(..., background=
            # True)`), sans appelant synchrone pour observer/relancer l'échec —
            # doit rester visible côté ops plutôt qu'avalé en silence (cf.
            # `LLMExtractor.extract`, même logique).
            logger.exception("MemoryManager._extract_and_persist: échec pour user_id=%s", user_id)
            return WriteReport()
        finally:
            session.close()

    def _maybe_compress(self, session: Session, user_id: str, thread: Thread) -> None:
        # Rebind défensif : appelée après le(s) `session.commit()` de
        # `_extract_and_persist`, qui peut avoir rendu la connexion au pool
        # (même bug que `_maybe_add_episode_guarded` — RLS rejette `upsert_fact`
        # plus bas si le GUC n'est plus posé sur la connexion courante).
        self._bind_user(session, user_id)
        if thread.token_count <= self.token_budget:
            return
        config: RunnableConfig = {"configurable": {"thread_id": thread.thread_id}}
        with self._graph_lock:
            snapshot = self._graph.get_state(config)
        all_messages = snapshot.values.get("messages", []) if snapshot.values else []
        keep_n = self.keep_last_n_turns * 2
        older = all_messages[:-keep_n] if len(all_messages) > keep_n else []
        if not older:
            return
        block = "\n".join(f"{m['role']}: {m['content']}" for m in older)

        # 1. Extraction préalable : l'info critique quitte le texte volatil avant résumé.
        extracted = self.extractor.extract(block, "")
        for ef in extracted.facts:
            if ef.confidence < self.confidence_threshold:
                continue
            if is_tombstoned(session, user_id, "fact_key", ef.key):
                continue
            # La capture "dispute" est tour-par-tour (message unique), pas bloc :
            # la ré-extraire sur `block` (concaténation multi-tours) corromprait la
            # valeur déjà capturée correctement à l'écriture.
            if ef.type == "dispute":
                continue
            fact, changed = upsert_fact(
                session, user_id, ef.key, ef.value, ef.type, ef.confidence, thread.thread_id
            )
            if changed:
                write_audit(session, user_id, "write", f"fact:{fact.key}", actor="extractor")
        for ep in extracted.procedures:
            if ep.confidence < self.confidence_threshold:
                continue
            if is_tombstoned(session, user_id, "procedure_trigger", ep.trigger):
                continue
            if not _rule_is_persistable(ep.trigger, ep.rule):
                continue
            _, changed = upsert_procedure(
                session, user_id, ep.trigger, ep.rule, ep.confidence, thread.thread_id
            )
            if changed:
                write_audit(session, user_id, "write", f"procedure:{ep.trigger}", actor="extractor")

        # 2. Résumé LLM (remplace la concaténation brute).
        try:
            summary = self.llm.invoke(SUMMARY_SYSTEM, "", block).strip()
        except Exception:
            # Panne réseau/filtre de contenu Azure sur le résumé : ne doit jamais
            # remonter jusqu'à `_extract_and_persist`, qui écraserait le rapport du
            # tour courant (déjà committé juste au-dessus) avec un `WriteReport`
            # vide (cf. `LLMExtractor.extract`, même logique). `thread.summary`
            # n'avance pas : nouvelle tentative sur ce bloc au prochain tour.
            logger.exception(
                "MemoryManager._maybe_compress: échec du résumé LLM pour user_id=%s", user_id
            )
            return

        # 3. Persistance. Le garde anti-résurrection est appelé D'ABORD, avant
        # d'avancer `thread.summary` : sinon un refus (résumé reprenant une
        # valeur sous tombstone) laisserait la chaîne interdite persister dans
        # `Thread.summary` — re-rendu par `read()` — même si l'épisode
        # correspondant n'a jamais été écrit (F1). On n'avance jamais moins que
        # ce tour (pas de retry en boucle sur le même bloc, cf. docstring de
        # cette méthode) : à la place, on expurge la valeur du texte persisté,
        # avec le même remplacement/matching que `_scrub_thread_messages`.
        tombstoned = self._active_value_tombstones_in(session, user_id, summary)
        persisted_summary = summary
        if tombstoned:
            logger.info(
                "résumé de compression expurgé pour user_id=%s : valeur sous tombstone",
                user_id,
            )
            for target in tombstoned:
                persisted_summary = re.sub(
                    rf"(?<!\w){re.escape(target)}(?!\w)",
                    "[information supprimée]",
                    persisted_summary,
                )
        thread.summary = (thread.summary + " " if thread.summary else "") + persisted_summary
        if not tombstoned:
            self._maybe_add_episode_guarded(session, user_id, summary, thread.thread_id)
        session.commit()

    def _scrub_thread_messages(self, user_id: str, value: str) -> None:
        """Remplace `value` par un texte neutre dans tous les threads actifs de
        l'utilisateur (scrub best-effort du fil court terme, cf. §R5 : purger le
        checkpoint est le défaut prod, ce scrub partiel n'est utilisé que pour
        les cas où la session doit continuer sans coupure — voir doc)."""
        session = self._Session()
        try:
            self._bind_user(session, user_id)
            threads = session.scalars(select(Thread).where(Thread.user_id == user_id)).all()
        finally:
            session.close()
        for thread in threads:
            config: RunnableConfig = {"configurable": {"thread_id": thread.thread_id}}
            with self._graph_lock:
                snapshot = self._graph.get_state(config)
                if not snapshot.values:
                    continue
                messages = snapshot.values.get("messages", [])
                scrubbed = [
                    {**m, "content": m["content"].replace(value, "[information supprimée]")}
                    for m in messages
                ]
                if scrubbed != messages:
                    self._graph.update_state(config, {"messages": replace_messages(scrubbed)})

    def remember_fact(self, user_id: str, key: str, value: str) -> None:
        session = self._Session()
        try:
            self._bind_user(session, user_id)
            get_or_create_user(session, user_id)
            self._bind_user(session, user_id)
            fact, _ = upsert_fact(session, user_id, key, value, "identity", 1.0, None)
            # Un "remember" explicite (ré-)établit délibérément la donnée : lever
            # un éventuel tombstone posé par un `forget()` antérieur sur cette
            # clé, sinon l'extracteur resterait bloqué indéfiniment sur `fact.key`
            # (cf. `is_tombstoned` dans `_extract_and_persist`/`_maybe_compress`)
            # alors qu'un fait vivant et à jour existe désormais pour cette clé.
            resolve_tombstone(session, user_id, "fact_key", fact.key)
            write_audit(session, user_id, "write", f"fact:{key}")
            session.commit()
        finally:
            session.close()

    def remember_procedure(self, user_id: str, trigger: str, rule: str) -> None:
        """Symétrique à `remember_fact` : établit explicitement une procédure
        et lève un éventuel tombstone `procedure_trigger` posé par un
        `forget()` antérieur, sinon l'extracteur resterait bloqué indéfiniment
        sur ce trigger alors qu'une règle vivante et à jour existe désormais
        (cf. `is_tombstoned` dans `_extract_and_persist`/`_maybe_compress`)."""
        session = self._Session()
        try:
            self._bind_user(session, user_id)
            get_or_create_user(session, user_id)
            self._bind_user(session, user_id)
            proc, _ = upsert_procedure(session, user_id, trigger, rule, 1.0, None)
            resolve_tombstone(session, user_id, "procedure_trigger", proc.trigger)
            write_audit(session, user_id, "write", f"procedure:{trigger}")
            session.commit()
        finally:
            session.close()

    def forget(self, user_id: str, target: str) -> ForgetReport:
        session = self._Session()
        try:
            self._bind_user(session, user_id)
            removed_facts = delete_facts_matching(session, user_id, target)
            report = ForgetReport(
                count=len(removed_facts),
                facts=[RemovedFact(key=f.key, value=f.value) for f in removed_facts],
            )
            for fact in removed_facts:
                set_tombstone(session, user_id, "fact_key", fact.key)
                set_tombstone(session, user_id, "fact_value", fact.value)
                removed_episodes = delete_episodes_matching(session, user_id, fact.value)
                report.count += len(removed_episodes)
                report.episodes.extend(e.summary for e in removed_episodes)
                for episode in removed_episodes:
                    self.episodic_store.delete(episode.id)
            removed_procs = delete_procedure_matching(session, user_id, target)
            report.count += len(removed_procs)
            report.procedures = [
                RemovedProcedure(trigger=p.trigger, rule=p.rule) for p in removed_procs
            ]
            for proc in removed_procs:
                # Levé explicitement par `remember_procedure` si l'utilisateur
                # revient sur son refus (même contrat que `fact_key`/`remember_fact`).
                set_tombstone(session, user_id, "procedure_trigger", proc.trigger)
            write_audit(session, user_id, "delete", target)
            session.commit()
        finally:
            session.close()

        for fact in removed_facts:
            self._scrub_thread_messages(user_id, fact.value)

        return report

    def forget_all(self, user_id: str) -> ForgetReport:
        """Droit à l'oubli total (`docs/reference/reco_expert.md`, R5) : purge toute la
        mémoire d'un utilisateur, court terme comme long terme.

        `forget(target)` ne cible qu'un élément ; ceci supprime tout — faits,
        procédures, épisodes (embedding pgvector inclus, même ligne),
        messages et threads — via la cascade DB sur `MemoryUser` (cf.
        `test_cascade_delete_removes_children_on_sqlite`), puis recrée un
        compte utilisateur vierge pour que l'agent reste utilisable et que
        l'opération elle-même soit journalisée (traçabilité).
        """
        session = self._Session()
        try:
            self._bind_user(session, user_id)
            facts = list_facts(session, user_id)
            procedures = list_procedures(session, user_id)
            episodes = list_episodes(session, user_id)
            # Collecte AVANT la cascade : les lignes Thread vont disparaître, mais
            # les checkpoints LangGraph vivent dans un store séparé (non FK) qu'il
            # faut purger explicitement, sinon le verbatim survit (B1).
            thread_ids = [t.thread_id for t in list_threads(session, user_id)]
            for episode in episodes:
                self.episodic_store.delete(episode.id)

            report = ForgetReport(
                count=len(facts) + len(procedures) + len(episodes),
                facts=[RemovedFact(key=f.key, value=f.value) for f in facts],
                procedures=[RemovedProcedure(trigger=p.trigger, rule=p.rule) for p in procedures],
                episodes=[e.summary for e in episodes],
            )

            # Store secondaire (checkpoints) purgé AVANT toute mutation métier —
            # même ordre que `purge_inactive_threads` (D9-10). Important : la
            # cascade métier n'attend pas le `session.commit()` final, elle est
            # déjà actée par le COMMIT interne de `get_or_create_user` juste en
            # dessous (recréation de l'utilisateur après suppression) — la purge
            # doit donc précéder CE point, pas seulement la fin de la fonction.
            # Un crash ici laisse les données métier intactes et un re-run
            # complète l'opération. Le checkpointer gère sa propre connexion,
            # indépendante de `session`.
            with self._graph_lock:
                for thread_id in thread_ids:
                    self._checkpointer.delete_thread(thread_id)

            user = session.get(MemoryUser, user_id)
            if user is not None:
                session.delete(user)
                session.flush()

            get_or_create_user(session, user_id)
            self._bind_user(session, user_id)
            # Tombstones posés APRÈS la recréation de l'utilisateur : sinon la
            # cascade FK (memory_tombstone.user_id ON DELETE CASCADE) les emporte
            # dans la même transaction et l'anti-résurrection est inopérante (B2).
            for fact in facts:
                set_tombstone(session, user_id, "fact_key", fact.key)
                set_tombstone(session, user_id, "fact_value", fact.value)
            for proc in procedures:
                set_tombstone(session, user_id, "procedure_trigger", proc.trigger)
            write_audit(session, user_id, "delete", "all")
            session.commit()
        finally:
            session.close()

        return report

    def clear_session(self, user_id: str) -> None:
        """Termine la conversation active sans toucher à la mémoire long terme.

        Équivalent d'un `/clear` : le thread courant (historique + résumé)
        sort de la fenêtre `session_gap_hours`, donc le prochain tour en
        recrée un vierge (cf. `get_or_create_active_thread`). Faits,
        procédures et épisodes ne sont pas touchés — contrairement à
        `forget_all` (droit à l'oubli, R5), ceci ne relève pas de la
        traçabilité RGPD et n'a donc pas besoin d'un `ForgetReport`.
        """
        session = self._Session()
        try:
            self._bind_user(session, user_id)
            thread = get_or_create_active_thread(session, user_id, self.session_gap_hours)
            thread.last_message_at = utcnow() - timedelta(hours=self.session_gap_hours, seconds=1)
            write_audit(session, user_id, "clear_session", thread.thread_id)
            session.commit()
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
