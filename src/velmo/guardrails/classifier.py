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


LLAMA_GUARD_CATEGORY_MAP: dict[str, str] = {
    "S1": "violence",  # Violent Crimes
    "S3": "sexual",  # Sex-Related Crimes
    "S4": "sexual",  # Child Sexual Exploitation
    "S10": "hate",  # Hate
    "S11": "violence",  # Suicide & Self-Harm — même convention que
    # `LexicalClassifier.VIOLENCE_PHRASES` (("faire", "mal")) : pas de
    # catégorie séparée pour l'auto-agression dans ce pipeline.
    "S12": "sexual",  # Sexual Content
}


def _parse_llama_guard_response(content: str) -> dict[str, float]:
    """Parse la réponse Llama Guard 3 (`"safe"` ou `"unsafe\\nS10,S11"`) vers
    nos 3 catégories. Codes MLCommons non mappés (S2, S5-S9, S13...) ignorés.
    """
    scores = {"hate": 0.0, "violence": 0.0, "sexual": 0.0}
    lines = content.strip().splitlines()
    if not lines or lines[0].strip().lower() != "unsafe":
        return scores
    codes = lines[1].split(",") if len(lines) > 1 else []
    for code in codes:
        category = LLAMA_GUARD_CATEGORY_MAP.get(code.strip())
        if category:
            scores[category] = 1.0
    return scores


class LlamaGuardClassifier:
    """Backend réel : Llama Guard 3 (Meta), servi localement via Ollama —
    modèle multilingue (FR inclus), taxonomie MLCommons S1-S13 mappée sur
    nos 3 catégories (`LLAMA_GUARD_CATEGORY_MAP`)."""

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        import os

        self._base_url = (base_url or os.environ["OLLAMA_URL"]).rstrip("/")
        self._model = model or os.getenv("LLAMA_GUARD_MODEL", "llama-guard3:8b")

    def score(self, text: str) -> dict[str, float]:
        import requests

        response = requests.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": text}],
                "stream": False,
            },
            timeout=8,
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content", "")
        return _parse_llama_guard_response(content)


class CombinedClassifier:
    """OR logique entre un backend sémantique (`LlamaGuardClassifier`) et
    `LexicalClassifier` — score = max par catégorie. Mesuré sur
    `eval/guardrail_cases.jsonl` : Llama Guard 3 8B seul rate encore 2 cas
    limpides (« sale race », « je vais te frapper ») que le lexique FR
    attrape trivialement ; le lexique comble cet angle mort, Llama Guard
    généralise au-delà du lexique fixe."""

    def __init__(self, primary: ModerationClassifier, lexical: LexicalClassifier | None = None) -> None:
        self._primary = primary
        self._lexical = lexical or LexicalClassifier()

    def score(self, text: str) -> dict[str, float]:
        primary_scores = self._primary.score(text)
        lexical_scores = self._lexical.score(text)
        return {
            category: max(primary_scores.get(category, 0.0), lexical_scores.get(category, 0.0))
            for category in ("hate", "violence", "sexual")
        }


def get_classifier() -> ModerationClassifier:
    """Llama Guard 3 (Ollama) combiné au repli lexical si `OLLAMA_URL` est
    configuré, sinon repli lexical seul."""
    import os

    if not os.getenv("OLLAMA_URL"):
        return LexicalClassifier()
    try:
        return CombinedClassifier(LlamaGuardClassifier())
    except Exception:
        return LexicalClassifier()
