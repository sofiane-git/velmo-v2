"""Étage 2 du pipeline garde-fous : classifieur de modération (G1/G2/G3).

`DetoxifyClassifier` est le backend réel (HuggingFace, local, ~90MB) ; en son
absence (poids/torch non installés — cas par défaut hors-ligne/CI),
`LexicalClassifier` sert de repli déterministe sur un lexique français ciblé
(pas un classifieur de modération exhaustif — cf. `docs/job/conceptions/
conception_chantier2_guardrails.md`, angles morts documentés).
"""

from __future__ import annotations

from typing import Protocol

from ._text import phrase_hit, tokens


class ModerationClassifier(Protocol):
    def score(self, text: str) -> dict[str, float]: ...


HATE_PHRASES: list[tuple[str, ...]] = [
    ("sous", "etre"),
    ("sous", "humain"),
    ("sale", "race"),
    ("hais",),
]
VIOLENCE_PHRASES: list[tuple[str, ...]] = [
    ("frapper",),
    ("vais", "tuer"),
    ("faire", "mal"),
]
SEXUAL_PHRASES: list[tuple[str, ...]] = [
    ("sexuel", "explicite"),
    ("nudite",),
]


class LexicalClassifier:
    """Repli déterministe (lexique FR), hors-ligne, aucune dépendance externe."""

    def score(self, text: str) -> dict[str, float]:
        toks = tokens(text)
        return {
            "hate": 1.0 if any(phrase_hit(toks, p) for p in HATE_PHRASES) else 0.0,
            "violence": 1.0 if any(phrase_hit(toks, p) for p in VIOLENCE_PHRASES) else 0.0,
            "sexual": 1.0 if any(phrase_hit(toks, p) for p in SEXUAL_PHRASES) else 0.0,
        }


class DetoxifyClassifier:
    """Backend réel : Detoxify (HuggingFace, modèle `original`, local)."""

    def __init__(self) -> None:
        from detoxify import Detoxify  # import différé : dépendance optionnelle

        self._model = Detoxify("original")

    def score(self, text: str) -> dict[str, float]:
        raw = self._model.predict(text)
        return {
            "hate": max(float(raw.get("identity_attack", 0.0)), float(raw.get("insult", 0.0))),
            "violence": max(float(raw.get("threat", 0.0)), float(raw.get("severe_toxicity", 0.0))),
            "sexual": float(raw.get("obscene", 0.0)),
        }


def get_classifier() -> ModerationClassifier:
    """Detoxify si les poids/torch sont disponibles, sinon le repli lexical."""
    try:
        return DetoxifyClassifier()
    except Exception:
        return LexicalClassifier()
