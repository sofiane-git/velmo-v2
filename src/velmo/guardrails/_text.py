"""Tokenisation partagée par les détecteurs lexicaux du pipeline garde-fous
(`patterns.py`, `classifier.py`, `judge.py`) : accents/casse normalisés,
correspondance par racine de mot (préfixe).
"""

from __future__ import annotations

import re
import unicodedata


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", strip_accents(text.lower())))


def phrase_hit(toks: set[str], phrase: tuple[str, ...]) -> bool:
    return all(any(tok.startswith(root) for tok in toks) for root in phrase)


def any_phrase(toks: set[str], phrases: list[tuple[str, ...]]) -> bool:
    return any(phrase_hit(toks, phrase) for phrase in phrases)
