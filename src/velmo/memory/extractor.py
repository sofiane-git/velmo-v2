"""Extraction déterministe des faits durables (règles/regex, pas d'appel LLM —
offline-safe). Interface `FactExtractor` swappable plus tard pour une variante
LLM sans changer `MemoryManager` ni le schéma.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _norm(s: str) -> str:
    return _strip_accents(s.lower())


@dataclass
class ExtractedFact:
    key: str
    value: str
    type: str
    confidence: float


class FactExtractor(Protocol):
    def extract(self, user_message: str, assistant_message: str) -> list[ExtractedFact]: ...


_SIZE_RE = re.compile(r"\b(XXL|XL|S|M|L)\b")
_ORDER_RE = re.compile(r"O-\d{4}-\d{4}")
_CONTRACT_RE = re.compile(r"C-\d+")

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
    """Extracteur par règles : un match = un score de confiance fixe."""

    def extract(self, user_message: str, assistant_message: str) -> list[ExtractedFact]:
        low = _norm(user_message)
        facts: list[ExtractedFact] = []

        if any(t in low for t in _SIZE_TRIGGERS):
            m = _SIZE_RE.search(user_message)
            if m:
                facts.append(ExtractedFact("shoe_size", m.group(1), "preference", 0.9))

        if any(t in low for t in _CLUB_TRIGGERS):
            m = _CLUBS_CAPTURE_RE.search(user_message)
            if m:
                facts.append(ExtractedFact("clubs", m.group(1).strip(" ."), "preference", 0.85))

        for phrase, segment in _SEGMENT_PHRASES.items():
            if _norm(phrase) in low:
                facts.append(ExtractedFact("segment", segment, "identity", 0.9))
                break

        if any(t in low for t in _TU_TRIGGERS):
            facts.append(ExtractedFact("address_mode", "tu", "preference", 0.95))
        elif any(t in low for t in _VOUS_TRIGGERS):
            facts.append(ExtractedFact("address_mode", "vous", "preference", 0.95))

        if "contrat" in low:
            m = _CONTRACT_RE.search(user_message)
            if m:
                facts.append(ExtractedFact("contract_number", m.group(0), "identity", 0.9))

        m = _ORDER_RE.search(user_message)
        if m:
            facts.append(ExtractedFact("order_number", m.group(0), "order", 0.85))

        if any(t in low for t in _ADDRESS_TRIGGERS):
            m = _ADDRESS_CAPTURE_RE.search(user_message)
            if m:
                facts.append(ExtractedFact("address", m.group(1).strip(" ."), "identity", 0.85))

        if any(t in low for t in _DISPUTE_TRIGGERS):
            facts.append(ExtractedFact("dispute", user_message.strip(), "dispute", 0.8))

        return facts
