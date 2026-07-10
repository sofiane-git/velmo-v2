"""Étage 1 du pipeline garde-fous : motifs déterministes (regex/lexical).

Coup net (`block`) sur les catégories qui se détectent à coup sûr par un motif
fixe : injection de prompt connue (G6), tentative d'extraction de secret (G7).
La PII structurée (G4 : carte + Luhn, mot de passe, IBAN) est vérifiée par
`scan_pii`, appelé uniquement en sortie par `pipeline.py` — ce module reste
sans connaissance du sens entrée/sortie.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass
class Hit:
    category: str
    method: str
    action: str  # "block" | "flag"
    score: float | None = None


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _strip_accents(text.lower())))


def _phrase_hit(tokens: set[str], phrase: tuple[str, ...]) -> bool:
    return all(any(tok.startswith(root) for tok in tokens) for root in phrase)


def _any_phrase(tokens: set[str], phrases: list[tuple[str, ...]]) -> bool:
    return any(_phrase_hit(tokens, phrase) for phrase in phrases)


INJECTION_PHRASES: list[tuple[str, ...]] = [
    ("ignore", "instruction"),
    ("oublie", "consigne"),
    ("developer", "mode"),
    ("mode", "developpeur"),
    ("prompt", "systeme"),
    ("plus", "regle"),
]

SECRET_PHRASES: list[tuple[str, ...]] = [
    ("cle", "api"),
    ("mot", "passe", "base"),
    ("environnement",),
    ("token", "interne"),
    ("secret", "configuration"),
    ("configuration", "interne"),
]

CARD_RE = re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b")
IBAN_RE = re.compile(r"\bFR\d{2}(?:\s?\d{2,4}){3,7}\b", re.IGNORECASE)
PASSWORD_RE = re.compile(r"mot\s+de\s+passe", re.IGNORECASE)
SECRET_KEY_RE = re.compile(r"\b(?:sk|xox[bap]|ghp|AKIA)[A-Za-z0-9_-]{10,}\b")


def luhn_valid(number: str) -> bool:
    """Vrai si `number` (chiffres, espaces/tirets tolérés) passe l'algorithme de Luhn."""
    digits = [int(d) for d in re.sub(r"\D", "", number)]
    if len(digits) < 12:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def scan_injection(text: str) -> Hit | None:
    """G6 — motifs d'injection connus. Court-circuite le pipeline (`block`)."""
    if _any_phrase(_tokens(text), INJECTION_PHRASES):
        return Hit(category="prompt_injection", method="regex", action="block")
    return None


def scan_secret_leak(text: str) -> Hit | None:
    """G7 — motifs de fuite/extraction de secret connus."""
    if _any_phrase(_tokens(text), SECRET_PHRASES) or SECRET_KEY_RE.search(text):
        return Hit(category="secret_leak", method="regex", action="block")
    return None


def scan_pii(text: str) -> Hit | None:
    """G4 — PII structurée (carte + Luhn, mot de passe, IBAN). Sortie uniquement."""
    card = CARD_RE.search(text)
    if card and luhn_valid(card.group(0)):
        return Hit(category="pii", method="regex", action="block")
    if PASSWORD_RE.search(text):
        return Hit(category="pii", method="regex", action="block")
    if IBAN_RE.search(text):
        return Hit(category="pii", method="regex", action="block")
    return None
