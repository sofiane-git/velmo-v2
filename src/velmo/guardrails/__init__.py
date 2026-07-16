"""Garde-fous d'entrée et de sortie de l'agent Velmo.

Surface publique stable consommée par l'agent et la suite d'acceptance :
`GuardrailEngine.check_input`/`check_output`, `Decision`, `CATEGORIES`.
Orchestre `patterns.py` (étage 1, regex), `classifier.py`/`judge.py`/
`prompt_shields.py`/`pii_redaction.py` (étages 2/3, via `pipeline.py`) et
`db.py` (journal `guardrail_audit`, isolé par utilisateur).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import sessionmaker

from . import pipeline
from .classifier import ModerationClassifier, get_classifier
from .db import bind_user, count_recent_audit, make_guardrails_engine, write_audit
from .judge import Judge, get_judge
from .patterns import redact_pii, redact_secret_leak

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


@dataclass
class Decision:
    """Verdict d'un garde-fou sur un message."""

    allowed: bool
    action: str  # "allow" | "block"
    category: str | None = None
    reason: str = ""
    refusal: str | None = None
    escalate: bool = False
    hits: list[pipeline.Hit] = field(default_factory=list)


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
        engine = make_guardrails_engine(db_url)
        self._Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        self.classifier = classifier or get_classifier()
        self.judge = judge or get_judge()

    def check_input(
        self, message: str, user_id: str | None = None, source_thread_id: str | None = None
    ) -> Decision:
        """Contrôle un message entrant (modération, injection, périmètre)."""
        return self._check(
            message, location="input", user_id=user_id, source_thread_id=source_thread_id
        )

    def check_output(
        self, text: str, user_id: str | None = None, source_thread_id: str | None = None
    ) -> Decision:
        """Contrôle une réponse sortante (PII, secrets, périmètre, modération)."""
        return self._check(
            text, location="output", user_id=user_id, source_thread_id=source_thread_id
        )

    def _check(
        self, text: str, *, location: str, user_id: str | None, source_thread_id: str | None
    ) -> Decision:
        hits = pipeline.run(text, location=location, classifier=self.classifier, judge=self.judge)

        session = self._Session()
        try:
            bind_user(session, user_id)
            for hit in hits:
                if hit.category not in CATEGORIES:
                    continue  # ex. "availability" : flag interne, pas une catégorie G1-G7
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

            blocking = next(
                (h for h in hits if h.action == "block" and h.category in CATEGORIES), None
            )
            relevant_hits = [h for h in hits if h.category in CATEGORIES]
            if blocking is None:
                return Decision(allowed=True, action="allow", hits=relevant_hits)

            escalate = blocking.category in ESCALATE_CATEGORIES
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
            )
        finally:
            session.close()
