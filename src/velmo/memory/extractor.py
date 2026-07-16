"""Extraction des faits et procédures durables.

Deux implémentations du Protocol `FactExtractor` :
- `RuleBasedExtractor` — déterministe (règles/regex), hors-ligne, utilisé par
  défaut et dans les tests ; ne produit jamais de procédures.
- `LLMExtractor` — délègue à un `LLM` (interface `velmo.llm.LLM`) qui renvoie du
  JSON validé par Pydantic ; produit des faits ET des procédures.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Protocol

from pydantic import BaseModel, Field

from velmo.llm import LLM
from velmo.memory.entities import CONTRACT_RE as _CONTRACT_RE
from velmo.memory.entities import ORDER_RE as _ORDER_RE

logger = logging.getLogger(__name__)


class ExtractedFact(BaseModel):
    key: str
    value: str
    type: str  # preference | identity | order | dispute
    confidence: float  # 0..1


class ExtractedProcedure(BaseModel):
    trigger: str  # contexte d'application, ex. "refund_offer"
    rule: str  # instruction en langage naturel
    confidence: float  # 0..1


class ExtractionResult(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list)
    procedures: list[ExtractedProcedure] = Field(default_factory=list)


class FactExtractor(Protocol):
    def extract(self, user_message: str, assistant_message: str) -> ExtractionResult: ...


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _norm(s: str) -> str:
    return _strip_accents(s.lower())


_SIZE_RE = re.compile(r"\b(XXL|XL|S|M|L)\b")

_SIZE_TRIGGERS = ("taille", "pointure")
_CLUB_TRIGGERS = ("club", "clubs", "equipe", "equipes")
_SEGMENT_PHRASES = {
    "revendeur": "revendeur",
    "client pro": "pro",
    "compte pro": "pro",
    "professionnel": "pro",
    "particulier": "particulier",
}
_TU_TRIGGERS = ("tutoie-moi", "tutoie moi", "tu peux me tutoyer")
_VOUS_TRIGGERS = ("vouvoie-moi", "vouvoie moi")
_ADDRESS_TRIGGERS = ("mon adresse", "adresse de livraison")
_DISPUTE_TRIGGERS = ("litige", "conteste", "contester", "faux", "authenticit", "contrefacon")

_CLUBS_CAPTURE_RE = re.compile(r"(?:clubs?|equipes?)[^.]*?(?:sont|est|:)\s*(.+)", re.IGNORECASE)
_ADDRESS_CAPTURE_RE = re.compile(r"adresse(?:\s+de\s+livraison)?\s+est\s+(.+)", re.IGNORECASE)


class RuleBasedExtractor:
    """Extracteur par règles : un match = un score de confiance fixe.

    Ne produit jamais de procédures (procédures = ressort du `LLMExtractor`).
    """

    def extract(self, user_message: str, assistant_message: str) -> ExtractionResult:
        low = _norm(user_message)
        facts: list[ExtractedFact] = []

        if any(t in low for t in _SIZE_TRIGGERS):
            m = _SIZE_RE.search(user_message)
            if m:
                facts.append(
                    ExtractedFact(
                        key="shoe_size", value=m.group(1), type="preference", confidence=0.9
                    )
                )

        if any(t in low for t in _CLUB_TRIGGERS):
            m = _CLUBS_CAPTURE_RE.search(user_message)
            if m:
                facts.append(
                    ExtractedFact(
                        key="clubs",
                        value=m.group(1).strip(" ."),
                        type="preference",
                        confidence=0.85,
                    )
                )

        for phrase, segment in _SEGMENT_PHRASES.items():
            if _norm(phrase) in low:
                facts.append(
                    ExtractedFact(key="segment", value=segment, type="identity", confidence=0.9)
                )
                break

        if any(t in low for t in _TU_TRIGGERS):
            facts.append(
                ExtractedFact(key="address_mode", value="tu", type="preference", confidence=0.95)
            )
        elif any(t in low for t in _VOUS_TRIGGERS):
            facts.append(
                ExtractedFact(key="address_mode", value="vous", type="preference", confidence=0.95)
            )

        if "contrat" in low:
            m = _CONTRACT_RE.search(user_message)
            if m:
                facts.append(
                    ExtractedFact(
                        key="contract_number", value=m.group(0), type="identity", confidence=0.9
                    )
                )

        m = _ORDER_RE.search(user_message)
        if m:
            facts.append(
                ExtractedFact(key="order_number", value=m.group(0), type="order", confidence=0.85)
            )

        if any(t in low for t in _ADDRESS_TRIGGERS):
            m = _ADDRESS_CAPTURE_RE.search(user_message)
            if m:
                facts.append(
                    ExtractedFact(
                        key="address",
                        value=m.group(1).strip(" ."),
                        type="identity",
                        confidence=0.85,
                    )
                )

        if any(t in low for t in _DISPUTE_TRIGGERS):
            facts.append(
                ExtractedFact(
                    key="dispute", value=user_message.strip(), type="dispute", confidence=0.8
                )
            )

        return ExtractionResult(facts=facts)


EXTRACTION_SYSTEM = (
    "Tu extrais la mémoire long terme d'une conversation de support boutique.\n"
    "Rends UNIQUEMENT un objet JSON de la forme :\n"
    '{"facts": [{"key": str, "value": str, '
    '"type": "preference|identity|order|dispute", "confidence": float}], '
    '"procedures": [{"trigger": str, "rule": str, "confidence": float}]}\n'
    "Garde le durable (préférences, identité, commandes, litiges, règles métier "
    "récurrentes), jette l'éphémère (salutations, small talk). "
    "La confidence (0..1) doit refléter honnêtement ta certitude. "
    "Si rien n'est durable, rends des listes vides.\n\n"
    "Exemples :\n"
    'UTILISATEUR: Bonjour !\nASSISTANT: Bonjour, comment puis-je vous aider ?\n'
    '→ {"facts": [], "procedures": []}\n\n'
    'UTILISATEUR: Je fais toujours du 44, notez-le pour mes prochaines commandes.\n'
    'ASSISTANT: Noté, taille 44 enregistrée.\n'
    '→ {"facts": [{"key": "taille", "value": "44", "type": "preference", '
    '"confidence": 0.95}], "procedures": []}\n\n'
    'UTILISATEUR: Je m\'appelle Karim Belhadj, commande #4471.\n'
    'ASSISTANT: Merci Karim, je retrouve votre commande #4471.\n'
    '→ {"facts": [{"key": "nom", "value": "Karim Belhadj", "type": "identity", '
    '"confidence": 0.9}, {"key": "commande", "value": "#4471", "type": "order", '
    '"confidence": 0.9}], "procedures": []}\n\n'
    'UTILISATEUR: La dernière fois le maillot sentait le renfermé, vérifiez avant '
    'expédition la prochaine fois.\nASSISTANT: Compris, on vérifiera systématiquement.\n'
    '→ {"facts": [{"key": "litige_odeur", "value": "maillot sentait le renfermé à '
    'réception", "type": "dispute", "confidence": 0.7}], '
    '"procedures": [{"trigger": "avant expédition", "rule": "vérifier absence '
    'd\'odeur", "confidence": 0.6}]}'
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class LLMExtractor:
    """Extracteur LLM : prompte pour du JSON, valide avec Pydantic.

    Toute réponse non parsable retombe sur un `ExtractionResult` vide — l'échec
    d'extraction ne doit jamais casser le flux d'écriture mémoire.
    """

    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    def extract(self, user_message: str, assistant_message: str) -> ExtractionResult:
        message = f"UTILISATEUR: {user_message}\nASSISTANT: {assistant_message}"
        try:
            raw = self._llm.invoke(EXTRACTION_SYSTEM, "", message) or ""
        except Exception:
            # Panne réseau/timeout du LLM d'extraction : ne doit jamais casser
            # le flux d'écriture mémoire (cf. docstring de la classe). Loggé
            # pour rester visible côté ops plutôt qu'avalé en silence.
            logger.exception("LLMExtractor.extract: échec de l'appel LLM")
            return ExtractionResult()
        match = _JSON_RE.search(raw)
        if match is None:
            return ExtractionResult()
        try:
            return ExtractionResult.model_validate_json(match.group(0))
        except ValueError:
            # pydantic.ValidationError hérite de ValueError ; couvre aussi le JSON malformé.
            return ExtractionResult()
