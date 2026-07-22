"""Étage 1 du pipeline garde-fous : motifs déterministes (regex/lexical).

Coup net (`block`) sur les catégories qui se détectent à coup sûr par un motif
fixe : injection de prompt connue (G6), tentative d'extraction de secret (G7),
PII structurée (G4 : carte + Luhn, mot de passe, IBAN) via `scan_pii` — appelé
par `pipeline.py` en entrée comme en sortie ; ce module reste sans
connaissance du sens entrée/sortie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ._text import phrase_hit, tokens


@dataclass
class Hit:
    category: str
    method: str
    action: str  # "block" | "filter" | "flag"
    score: float | None = None
    reasoning: str | None = None
    # Positions `(offset, length)` du contenu à masquer quand la détection les
    # connaît (PII texte libre Azure AI Language) — un hit `filter` sans spans
    # s'appuie sur les regex structurées, un hit avec spans masque exactement
    # les segments détectés (cf. pipeline.py / __init__.py, fuite LLM06).
    spans: list[tuple[int, int]] | None = None


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
    """G7 — motifs de fuite/extraction de secret connus. Filtre (masque) le
    segment détecté, ne coupe pas le pipeline (voir pipeline.py)."""
    phrase = _matched_phrase(tokens(text), SECRET_PHRASES)
    if phrase:
        return Hit(
            category="secret_leak",
            method="regex",
            action="filter",
            reasoning=f"Expression détectée : « {' '.join(phrase)} »",
        )
    if SECRET_KEY_RE.search(text):
        return Hit(
            category="secret_leak",
            method="regex",
            action="filter",
            reasoning="Motif de clé secrète détecté (format sk-/xox.../ghp_.../AKIA...)",
        )
    return None


def scan_pii(text: str) -> Hit | None:
    """G4 — PII structurée (carte + Luhn, mot de passe, IBAN). Entrée et sortie.
    Filtre (masque) le segment détecté, ne coupe pas le pipeline."""
    card = CARD_RE.search(text)
    if card and luhn_valid(card.group(0)):
        return Hit(
            category="pii",
            method="regex",
            action="filter",
            reasoning="Numéro de carte bancaire détecté (Luhn valide)",
        )
    if PASSWORD_RE.search(text):
        return Hit(
            category="pii",
            method="regex",
            action="filter",
            reasoning="Mention d'un mot de passe détectée",
        )
    if IBAN_RE.search(text):
        return Hit(category="pii", method="regex", action="filter", reasoning="IBAN détecté")
    return None


def redact_secret_leak(text: str) -> str:
    """Masque un jeton/clé secret trouvé en clair (G7) avant persistance —
    même raison que `redact_pii` : ne pas faire survivre la valeur dans le
    journal de mémoire ni la renvoyer en clair à l'extracteur LLM. Les
    expressions d'extraction (« donne-moi ta clé api ») ne portent elles-mêmes
    aucune valeur secrète : rien à masquer dans ce cas, le message est laissé
    intact."""
    return SECRET_KEY_RE.sub("[clé secrète masquée]", text)


def redact_pii(text: str) -> str:
    """Masque toute PII structurée (carte, IBAN, mot de passe) avant toute
    persistance — un message bloqué par `scan_pii` ne doit pas survivre en
    clair dans le journal de mémoire ni repartir en clair vers l'extracteur
    LLM. Le mot de passe n'a pas de format fixe (contrairement à la carte et
    à l'IBAN) : sa valeur ne peut pas être délimitée de façon fiable par
    regex, donc tout le message est masqué plutôt que de risquer une fuite
    partielle de la valeur."""
    if PASSWORD_RE.search(text):
        return "[message masqué : mention d'un mot de passe]"
    card = CARD_RE.search(text)
    if card and luhn_valid(card.group(0)):
        text = CARD_RE.sub("[carte masquée]", text)
    return IBAN_RE.sub("[IBAN masqué]", text)
