"""Étage 2 du pipeline garde-fous : classifieur de modération (G1/G2/G3).

`LlamaGuardClassifier` (via Ollama) est le backend réel, combiné en OR avec
`LexicalClassifier` (repli déterministe, toujours disponible) dans
`CombinedClassifier`. `score()` reste l'API historique (scores seuls) ;
`score_detailed()` ajoute un raisonnement par catégorie sans dupliquer la
logique de détection (`score()` délègue à `score_detailed()`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ._scoring import FALLBACK_MAX_SCORE
from ._text import phrase_hit, tokens
from ._timeouts import CLIENT_TIMEOUT_S


@dataclass
class ClassifierResult:
    scores: dict[str, float]
    reasoning: dict[str, str]  # uniquement les catégories avec un score > 0


class ModerationClassifier(Protocol):
    def score(self, text: str) -> dict[str, float]: ...
    def score_detailed(self, text: str) -> ClassifierResult: ...


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


def _first_match(toks: set[str], phrases: list[tuple[str, ...]]) -> tuple[str, ...] | None:
    for phrase in phrases:
        if phrase_hit(toks, phrase):
            return phrase
    return None


class LexicalClassifier:
    """Repli déterministe (lexique FR), hors-ligne, aucune dépendance externe."""

    def score(self, text: str) -> dict[str, float]:
        return self.score_detailed(text).scores

    def score_detailed(self, text: str) -> ClassifierResult:
        toks = tokens(text)
        scores: dict[str, float] = {}
        reasoning: dict[str, str] = {}
        for category, phrases in (
            ("hate", HATE_PHRASES),
            ("violence", VIOLENCE_PHRASES),
            ("sexual", SEXUAL_PHRASES),
        ):
            match = _first_match(toks, phrases)
            scores[category] = FALLBACK_MAX_SCORE if match else 0.0
            if match:
                reasoning[category] = f"Expression détectée : « {' '.join(match)} »"
        return ClassifierResult(scores=scores, reasoning=reasoning)


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


def _llama_guard_reasoning(content: str, scores: dict[str, float]) -> dict[str, str]:
    lines = content.strip().splitlines()
    if not lines or lines[0].strip().lower() != "unsafe":
        return {}
    codes = [c.strip() for c in (lines[1].split(",") if len(lines) > 1 else [])]
    reasoning: dict[str, str] = {}
    for category, score in scores.items():
        if score <= 0:
            continue
        matched = [c for c in codes if LLAMA_GUARD_CATEGORY_MAP.get(c) == category]
        reasoning[category] = f"Llama Guard 3 : unsafe ({', '.join(matched)})"
    return reasoning


class LlamaGuardClassifier:
    """Backend réel : Llama Guard 3 (Meta), servi localement via Ollama —
    modèle multilingue (FR inclus), taxonomie MLCommons S1-S13 mappée sur
    nos 3 catégories (`LLAMA_GUARD_CATEGORY_MAP`)."""

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        import os

        self._base_url = (base_url or os.environ["OLLAMA_URL"]).rstrip("/")
        self._model = model or os.getenv("LLAMA_GUARD_MODEL", "llama-guard3:8b")

    def score(self, text: str) -> dict[str, float]:
        return self.score_detailed(text).scores

    def score_detailed(self, text: str) -> ClassifierResult:
        import requests

        response = requests.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": text}],
                "stream": False,
            },
            # Le premier appel après démarrage/inactivité d'Ollama charge le
            # modèle en mémoire (mesuré ~21-27s) — un timeout de 8s ici
            # abandonnait systématiquement ce premier appel (confirmé par les
            # logs Ollama : réponses 499, connexion fermée côté client à ~8s).
            # `CLIENT_TIMEOUT_S` (< CALL_TIMEOUT_S) garantit que ce timeout se
            # déclenche toujours avant l'abandon côté pipeline, qui lui ne tue
            # pas le thread (cf. pipeline.py).
            timeout=CLIENT_TIMEOUT_S,
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content", "")
        scores = _parse_llama_guard_response(content)
        reasoning = _llama_guard_reasoning(content, scores)
        return ClassifierResult(scores=scores, reasoning=reasoning)


class CombinedClassifier:
    """OR logique entre un backend sémantique (`LlamaGuardClassifier`) et
    `LexicalClassifier` — score = max par catégorie. Mesuré sur
    `eval/guardrail_cases.jsonl` : Llama Guard 3 8B seul rate encore 2 cas
    limpides (« sale race », « je vais te frapper ») que le lexique FR
    attrape trivialement ; le lexique comble cet angle mort, Llama Guard
    généralise au-delà du lexique fixe."""

    def __init__(
        self, primary: ModerationClassifier, lexical: LexicalClassifier | None = None
    ) -> None:
        self._primary = primary
        self._lexical = lexical or LexicalClassifier()

    def score(self, text: str) -> dict[str, float]:
        return self.score_detailed(text).scores

    def score_detailed(self, text: str) -> ClassifierResult:
        primary_result = self._primary.score_detailed(text)
        lexical_result = self._lexical.score_detailed(text)
        scores: dict[str, float] = {}
        reasoning: dict[str, str] = {}
        for category in ("hate", "violence", "sexual"):
            p_score = primary_result.scores.get(category, 0.0)
            l_score = lexical_result.scores.get(category, 0.0)
            scores[category] = max(p_score, l_score)
            if scores[category] <= 0:
                continue
            if p_score >= l_score and category in primary_result.reasoning:
                reasoning[category] = primary_result.reasoning[category]
            elif category in lexical_result.reasoning:
                reasoning[category] = lexical_result.reasoning[category]
        return ClassifierResult(scores=scores, reasoning=reasoning)


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
