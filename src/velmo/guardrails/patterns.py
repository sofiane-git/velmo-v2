"""Étage 1 du pipeline garde-fous : motifs déterministes (regex/lexical).

Coup net (`block`) sur les catégories qui se détectent à coup sûr par un motif
fixe : injection de prompt connue (G6), tentative d'extraction de secret (G7).
La PII structurée (G4 : carte + Luhn, mot de passe, IBAN) est vérifiée par
`scan_pii`, appelé uniquement en sortie par `pipeline.py` — ce module reste
sans connaissance du sens entrée/sortie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ._text import phrase_hit, tokens


@dataclass
class Hit:
    category: str
    method: str
    action: str  # "block" | "flag"
    score: float | None = None
    reasoning: str | None = None


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


def _matched_phrase(toks: set[str], phrases: list[tuple[str, ...]]) -> tuple[str, ...] | None:
    for phrase in phrases:
        if phrase_hit(toks, phrase):
            return phrase
    return None


def scan_injection(text: str) -> Hit | None:
    """G6 — motifs d'injection connus. Court-circuite le pipeline (`block`)."""
    phrase = _matched_phrase(tokens(text), INJECTION_PHRASES)
    if phrase:
        return Hit(
            category="prompt_injection",
            method="regex",
            action="block",
            reasoning=f"Expression détectée : « {' '.join(phrase)} »",
        )
    return None


def scan_secret_leak(text: str) -> Hit | None:
    """G7 — motifs de fuite/extraction de secret connus."""
    phrase = _matched_phrase(tokens(text), SECRET_PHRASES)
    if phrase:
        return Hit(
            category="secret_leak",
            method="regex",
            action="block",
            reasoning=f"Expression détectée : « {' '.join(phrase)} »",
        )
    if SECRET_KEY_RE.search(text):
        return Hit(
            category="secret_leak",
            method="regex",
            action="block",
            reasoning="Motif de clé secrète détecté (format sk-/xox.../ghp_.../AKIA...)",
        )
    return None


def scan_pii(text: str) -> Hit | None:
    """G4 — PII structurée (carte + Luhn, mot de passe, IBAN). Sortie uniquement."""
    card = CARD_RE.search(text)
    if card and luhn_valid(card.group(0)):
        return Hit(
            category="pii",
            method="regex",
            action="block",
            reasoning="Numéro de carte bancaire détecté (Luhn valide)",
        )
    if PASSWORD_RE.search(text):
        return Hit(
            category="pii",
            method="regex",
            action="block",
            reasoning="Mention d'un mot de passe détectée",
        )
    if IBAN_RE.search(text):
        return Hit(category="pii", method="regex", action="block", reasoning="IBAN détecté")
    return None
