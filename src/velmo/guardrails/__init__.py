"""Garde-fous d'entrée et de sortie de l'agent Velmo.

Surface publique stable consommée par l'agent et la suite d'acceptance :
`GuardrailEngine.check_input`/`check_output`, `Decision`, `CATEGORIES`.
Orchestre `patterns.py` (étage 1, regex), `classifier.py`/`judge.py`/
`prompt_shields.py`/`pii_redaction.py` (étages 2/3, via `pipeline.py`) et
`db.py` (journal `guardrail_audit`, isolé par utilisateur).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import sessionmaker

from velmo.config import Settings, get_settings

from . import pipeline
from .classifier import ModerationClassifier, get_classifier
from .db import bind_user, count_recent_audit, make_guardrails_engine, write_audit
from .judge import Judge, get_judge
from . import retrieved
from .patterns import redact_pii, redact_secret_leak
from .pii_redaction import redact_spans

logger = logging.getLogger(__name__)

__all__ = [
    "GENERIC_REFUSAL",
    "CATEGORIES",
    "Decision",
    "GuardrailEngine",
    "redact_pii",
    "redact_secret_leak",
]

# Catégories de contenus contrôlés.
CATEGORIES = (
    "hate",
    "violence",
    "sexual",
    "pii",
    "out_of_scope",
    "prompt_injection",
    "secret_leak",
    # G8 — injection indirecte (contenu récupéré, écriture mémoire). Contrôlée
    # hors du pipeline à 3 étages : elle n'a de sens ni en `input` ni en
    # `output`, mais aux points `retrieved` et `memory_write`
    # (`check_retrieved` / `check_memory_write`).
    "indirect_injection",
)

GENERIC_REFUSAL = (
    "Désolé, je ne peux pas répondre à cette demande. Je reste à votre "
    "disposition pour vos commandes, livraisons, retours et la FAQ Velmo."
)

# Messages par catégorie pour les cas majoritairement déclenchés par des
# demandes légitimes (périmètre, données sensibles) — un refus plus explicite
# aide l'utilisateur à reformuler. Les catégories adverses (hate, violence,
# sexual, prompt_injection) gardent GENERIC_REFUSAL à dessein : un message
# uniforme évite de renseigner un attaquant sur le motif exact du blocage.
REFUSAL_MESSAGES: dict[str, str] = {
    "out_of_scope": (
        "Cette question sort de mon périmètre : je peux vous aider pour vos "
        "commandes, livraisons, retours et la FAQ Velmo (maillots vintage, "
        "tailles, authenticité...). N'hésitez pas à me la reposer sous cet angle !"
    ),
    "pii": (
        "Je ne peux pas traiter cette demande telle quelle car elle contient des "
        "informations sensibles (carte bancaire, mot de passe, IBAN...). "
        "Reformulez sans cette donnée et je vous aide volontiers."
    ),
    "secret_leak": (
        "Je ne peux pas partager ce type d'information technique interne. Je "
        "reste à votre disposition pour vos commandes, livraisons, retours et "
        "la FAQ Velmo."
    ),
}

ESCALATE_CATEGORIES = ("violence", "secret_leak")
# Un coup isolé de ces catégories ne remonte pas (refus poli + log suffit,
# cf. seuils d'escalade métier : remboursement > 50 €, litige d'authenticité).
# Une répétition du même user_id devient un signal d'attaque/harcèlement
# actif — même logique que l'injection de prompt répétée.
REPEAT_ESCALATE_CATEGORIES = ("prompt_injection", "hate", "sexual")
REPEAT_THRESHOLD = 3
REPEAT_WINDOW = timedelta(hours=24)

# Actions considérées comme un blocage pour le choix du hit "bloquant" et
# l'audit de récidive — "block_escalate" (pipeline.py, ESCALATE_THRESHOLD)
# est un block avec un signal de confiance en plus, pas une catégorie à part.
BLOCKING_ACTIONS = ("block", "block_escalate")


@dataclass
class Decision:
    """Verdict d'un garde-fou sur un message."""

    allowed: bool
    action: str  # "allow" | "block" | "filter"
    category: str | None = None
    reason: str = ""
    refusal: str | None = None
    filtered_text: str | None = None  # texte masqué si action == "filter"
    # Texte à persister par l'appelant (agent), déjà redacté par l'engine selon
    # la décision — l'appelant ne re-dispatch PAS sur la catégorie pour choisir
    # le masquage (fuite de connaissance guardrails→agent, D3-05).
    stored_text: str | None = None
    escalate: bool = False
    hits: list[pipeline.Hit] = field(default_factory=list)


def _warn_unconfigured_stages(settings: Settings) -> None:
    """Journalise au démarrage les étages 2/3 non configurés / dégradés — une
    dégradation ne doit jamais être silencieuse (D4-05). Baseline manquante =
    `warning` ; feature-flag optionnel absent = `info`."""
    if not settings.ollama_url:
        logger.warning(
            "Garde-fous : OLLAMA_URL absent — classifieur en repli lexical seul (G1/G2/G3 dégradé)."
        )
    if not (settings.azure_openai_guard_endpoint and settings.azure_openai_guard_api_key):
        logger.warning(
            "Garde-fous : juge cloud non configuré — RuleBasedJudge seul (G5/G6/G7 dégradé)."
        )
    if not (settings.azure_content_safety_endpoint and settings.azure_content_safety_key):
        logger.info("Garde-fous : Prompt Shields non configuré (renfort G6 désactivé).")
    if not (settings.azure_language_endpoint and settings.azure_language_key):
        logger.info(
            "Garde-fous : PII redaction (Azure AI Language) non configurée "
            "(G4 texte libre en sortie désactivé)."
        )


class GuardrailEngine:
    """Applique les garde-fous d'entrée et de sortie et journalise les décisions."""

    def __init__(
        self,
        *,
        db_url: str | None = None,
        classifier: ModerationClassifier | None = None,
        judge: Judge | None = None,
    ) -> None:
        self.events: list[dict[str, Any]] = []
        self.engine = make_guardrails_engine(db_url)
        self._Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        _warn_unconfigured_stages(get_settings())
        self.classifier = classifier or get_classifier()
        self.judge = judge or get_judge()

    def close(self) -> None:
        """Dispose le pool de connexions — à appeler quand cette instance est
        jetable (ex. un agent frais par cas, `mlops/suites/tools.py`), sinon
        chaque pool reste ouvert jusqu'au GC (constaté en prod : épuisement
        des connexions Postgres, cf. `MemoryManager.close`)."""
        self.engine.dispose()

    def check_input(
        self, message: str, user_id: str | None = None, source_thread_id: str | None = None
    ) -> Decision:
        """Contrôle un message entrant (modération, injection, périmètre)."""
        return self._check(
            message, location="input", user_id=user_id, source_thread_id=source_thread_id
        )

    def check_output(
        self,
        text: str,
        user_id: str | None = None,
        source_thread_id: str | None = None,
        *,
        own_facts: dict[str, str] | None = None,
    ) -> Decision:
        """Contrôle une réponse sortante (PII, secrets, périmètre, modération).

        `own_facts` : faits mémoire de l'utilisateur courant (cf.
        `MemoryContext.facts`), utilisés pour le cross-check G4 (identifiant
        d'un autre client détecté dans la réponse). `None` désactive ce
        contrôle spécifique (mémoire non disponible à l'appel) sans affecter
        les autres catégories.
        """
        decision = self._check(
            text, location="output", user_id=user_id, source_thread_id=source_thread_id
        )
        if own_facts is None or decision.action == "block":
            # Un blocage complet remplace déjà toute la réponse par un refus —
            # inutile de chercher un identifiant étranger dans un texte qui ne
            # sera jamais renvoyé au client.
            return decision

        from .cross_check import find_foreign_identifiers, redact_foreign_identifiers

        foreign = find_foreign_identifiers(text, own_facts)
        if not foreign:
            return decision

        session = self._Session()
        try:
            bind_user(session, user_id)
            write_audit(
                session, user_id, "pii", "output", "cross_check", None, "filter", source_thread_id
            )
            session.commit()
        finally:
            session.close()

        filtered = redact_foreign_identifiers(decision.filtered_text or text, own_facts)
        return Decision(
            allowed=True,
            action="filter",
            category="pii",
            filtered_text=filtered,
            stored_text=filtered,
            hits=decision.hits,
        )

    def check_retrieved(self, text: str, *, source: str) -> Decision:
        """Porte `GRET` — G8 sur un contenu **récupéré** (extrait de FAQ, champ
        libre d'une commande, retour d'outil) avant qu'il n'entre dans le
        contexte de l'agent.

        Trois différences assumées avec `check_input`/`check_output` :

        - **l'action est l'écartement, jamais le blocage du tour.** L'auteur du
          contenu n'est pas le client : bloquer transformerait un document
          empoisonné en déni de service sur toutes les questions qui le
          touchent. On répond sans l'extrait.
        - **la délimitation passe avant la détection** : un contenu propre
          ressort *emballé* en donnée citée, pas simplement « autorisé ».
        - **fail-open sur le jugement, fail-closed sur la délimitation** : la
          neutralisation et l'emballage sont locaux et déterministes, ils ne
          tombent jamais — c'est ce qui rend l'absence de juge tolérable ici,
          contrairement à G5/G6 en entrée.
        """
        found = retrieved.find_instruction_patterns(text)
        if found:
            self._log_g8(source_kind=f"retrieved:{source}", location="retrieved", action="filter")
            return Decision(
                allowed=True,
                action="filter",
                category="indirect_injection",
                reason=f"Contenu récupéré écarté — forme d'instruction détectée : {found[0]!r}",
                filtered_text="",
                stored_text="",
            )
        return Decision(
            allowed=True,
            action="allow",
            filtered_text=retrieved.wrap_as_quoted_data(text, source=source),
        )

    def check_memory_write(self, value: str, *, kind: str) -> Decision:
        """Contrôle une écriture mémoire **candidate** avant persistance — G8,
        menace T5.

        `kind="procedure"` est le cas critique : la règle sera réinjectée dans le
        prompte système des sessions suivantes, donc une règle malveillante
        pilote l'agent bien après le tour où elle a été soufflée. `kind="fact"`
        est une donnée réinjectée comme contexte : on n'y applique que le
        contrôle de forme d'instruction, pas la grammaire de règle.

        **Fail-closed** : en cas de doute, on ne persiste pas. L'asymétrie des
        coûts est nette — une écriture refusée est récupérable (l'extraction est
        best-effort, le client peut redonner l'information explicitement), une
        écriture malveillante persistée ne l'est pas.
        """
        if kind == "procedure":
            ok, why = retrieved.rule_is_wellformed(value)
        else:
            found = retrieved.find_instruction_patterns(value)
            ok, why = (
                (not found),
                (f"forme d'instruction système détectée : {found[0]!r}" if found else ""),
            )
        if ok:
            return Decision(allowed=True, action="allow")
        self._log_g8(source_kind=f"memory_write:{kind}", location="memory_write", action="block")
        return Decision(
            allowed=False,
            action="block",
            category="indirect_injection",
            reason=f"Écriture mémoire refusée ({kind}) — {why}",
        )

    def _log_g8(self, *, source_kind: str, location: str, action: str) -> None:
        """Journalise un événement G8. `category='indirect_injection'` et les
        `location` `retrieved`/`memory_write` sont des valeurs de première classe
        du journal (cf. conception §Modèle de données), pas des cas spéciaux."""
        self.events.append(
            {
                "category": "indirect_injection",
                "location": location,
                "method": "regex",
                "action": action,
                "source_kind": source_kind,
            }
        )
        logger.warning("Garde-fous G8 : %s — action=%s (%s).", location, action, source_kind)

    @staticmethod
    def _redact_for_storage(text: str, category: str | None) -> str:
        """Texte à persister sans laisser survivre une valeur sensible en clair
        — l'engine porte la connaissance guardrails, pas l'agent (D3-05)."""
        if category == "pii":
            return redact_pii(text)
        if category == "secret_leak":
            return redact_secret_leak(text)
        return text

    def _check(
        self, text: str, *, location: str, user_id: str | None, source_thread_id: str | None
    ) -> Decision:
        hits = pipeline.run(text, location=location, classifier=self.classifier, judge=self.judge)

        session = self._Session()
        try:
            bind_user(session, user_id)
            for hit in hits:
                if hit.category not in CATEGORIES:
                    continue  # défense en profondeur : toute catégorie hors G1-G7 est ignorée
                self.events.append(
                    {
                        "user_id": user_id,
                        "category": hit.category,
                        "location": location,
                        "method": hit.method,
                        "score": hit.score,
                        "action": hit.action,
                        "source_thread_id": source_thread_id,
                    }
                )
                write_audit(
                    session,
                    user_id,
                    hit.category,
                    location,
                    hit.method,
                    hit.score,
                    hit.action,
                    source_thread_id,
                )
            session.commit()

            relevant_hits = [h for h in hits if h.category in CATEGORIES]
            blocking = next((h for h in relevant_hits if h.action in BLOCKING_ACTIONS), None)
            if blocking is not None:
                escalate = (
                    blocking.category in ESCALATE_CATEGORIES or blocking.action == "block_escalate"
                )
                if blocking.category in REPEAT_ESCALATE_CATEGORIES and user_id is not None:
                    count = count_recent_audit(session, user_id, blocking.category, REPEAT_WINDOW)
                    escalate = escalate or count >= REPEAT_THRESHOLD
                return Decision(
                    allowed=False,
                    action="block",
                    category=blocking.category,
                    refusal=REFUSAL_MESSAGES.get(blocking.category, GENERIC_REFUSAL),
                    escalate=escalate,
                    hits=relevant_hits,
                    stored_text=self._redact_for_storage(text, blocking.category),
                )

            filtering = [h for h in relevant_hits if h.action == "filter"]
            if filtering:
                filtered_text = text
                # Spans PII (texte libre Azure) d'abord : offsets calculés sur le
                # texte d'origine, appliqués avant tout masquage regex qui en
                # changerait la longueur (D4-03).
                spans = [s for h in filtering if h.category == "pii" and h.spans for s in h.spans]
                if spans:
                    filtered_text = redact_spans(filtered_text, spans)
                for hit in filtering:
                    if hit.category == "pii":
                        filtered_text = redact_pii(filtered_text)
                    elif hit.category == "secret_leak":
                        filtered_text = redact_secret_leak(filtered_text)
                # Une fuite confirmée (secret_leak) reste masquée côté client
                # mais doit alerter l'équipe sécurité (canal "security", cf.
                # Task 8) — le simple PII (G4) masqué n'a pas besoin d'alerte.
                escalate = any(h.category in ESCALATE_CATEGORIES for h in filtering)
                return Decision(
                    allowed=True,
                    action="filter",
                    category=filtering[0].category,
                    filtered_text=filtered_text,
                    stored_text=filtered_text,
                    escalate=escalate,
                    hits=relevant_hits,
                )

            return Decision(allowed=True, action="allow", hits=relevant_hits, stored_text=text)
        finally:
            session.close()
