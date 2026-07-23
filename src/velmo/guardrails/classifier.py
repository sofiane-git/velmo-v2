"""Étage 2 du pipeline garde-fous : classifieur de modération (G1/G2/G3).

`LlamaGuardClassifier` (via Ollama) est le backend réel, combiné en OR avec
`LexicalClassifier` (repli déterministe, toujours disponible) dans
`CombinedClassifier`. `score()` reste l'API historique (scores seuls) ;
`score_detailed()` ajoute un raisonnement par catégorie sans dupliquer la
logique de détection (`score()` délègue à `score_detailed()`).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Protocol

from velmo.config import get_settings, require

from ._scoring import FALLBACK_MAX_SCORE
from ._text import phrase_hit, tokens
from ._timeouts import CLIENT_TIMEOUT_S

logger = logging.getLogger(__name__)


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


def _extract_p_unsafe(logprobs: list[dict[str, Any]] | None) -> float | None:
    """Probabilité que Llama Guard 3 ait choisi `unsafe` plutôt que `safe`
    comme premier token généré, par softmax entre les deux logprobs. `None`
    si l'un des deux tokens n'apparaît pas dans le top-`top_logprobs` (pas de
    masse de probabilité exploitable pour normaliser) — l'appelant retombe
    alors sur le comportement binaire existant.
    """
    if not logprobs:
        return None
    first = logprobs[0]
    candidates: list[dict[str, Any]] = [first]
    top = first.get("top_logprobs")
    if isinstance(top, list):
        candidates.extend(top)
    by_token: dict[str, float] = {}
    for entry in candidates:
        token = str(entry.get("token", "")).strip().lower()
        if token and token not in by_token:
            by_token[token] = float(entry.get("logprob", float("-inf")))
    if "safe" not in by_token or "unsafe" not in by_token:
        return None
    logprob_safe = by_token["safe"]
    logprob_unsafe = by_token["unsafe"]
    return math.exp(logprob_unsafe) / (math.exp(logprob_unsafe) + math.exp(logprob_safe))


def _parse_llama_guard_response(content: str, p_unsafe: float = 1.0) -> dict[str, float]:
    """Parse la réponse Llama Guard 3 (`"safe"` ou `"unsafe\\nS10,S11"`) vers
    nos 3 catégories. Codes MLCommons non mappés (S2, S5-S9, S13...) ignorés.
    `p_unsafe` (calculé par `_extract_p_unsafe` quand les logprobs sont
    disponibles) remplace le `1.0` implicite d'origine — permet une vraie
    gradation au lieu d'un verdict tout-ou-rien.
    """
    scores = {"hate": 0.0, "violence": 0.0, "sexual": 0.0}
    lines = content.strip().splitlines()
    if not lines or lines[0].strip().lower() != "unsafe":
        return scores
    codes = lines[1].split(",") if len(lines) > 1 else []
    for code in codes:
        category = LLAMA_GUARD_CATEGORY_MAP.get(code.strip())
        if category:
            scores[category] = p_unsafe
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

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        latency_threshold_ms: float | None = None,
    ) -> None:
        settings = get_settings()
        self._base_url = require(base_url or settings.ollama_url, "OLLAMA_URL").rstrip("/")
        self._model = model or settings.llama_guard_model
        self._latency_threshold_ms = (
            latency_threshold_ms
            if latency_threshold_ms is not None
            else settings.llama_guard_latency_threshold_ms
        )
        self.last_latency_ms: float | None = None

    def score(self, text: str) -> dict[str, float]:
        return self.score_detailed(text).scores

    def score_detailed(self, text: str) -> ClassifierResult:
        import requests

        start = time.monotonic()
        response = requests.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": text}],
                "stream": False,
                # Ollama calcule déjà ces logprobs en interne — les demander
                # ne coûte rien de plus qu'un champ dans la réponse (cf.
                # docs.ollama.com/api/chat, testé en juillet 2026). Sert à
                # extraire un p_unsafe calibré au lieu du 1.0/0.0 binaire.
                "logprobs": True,
                "top_logprobs": 5,
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
        self.last_latency_ms = (time.monotonic() - start) * 1000
        if self.last_latency_ms > self._latency_threshold_ms:
            logger.warning(
                "LlamaGuardClassifier : latence %.0fms > seuil %.0fms (modèle %s) — "
                "envisager la bascule vers llama-guard3:1b (voir docs/reference/"
                "conceptions/conception_chantier2_guardrails.md §Seuil de bascule).",
                self.last_latency_ms,
                self._latency_threshold_ms,
                self._model,
            )
        response.raise_for_status()
        body = response.json()
        content = body.get("message", {}).get("content", "")
        p_unsafe = _extract_p_unsafe(body.get("logprobs"))
        scores = _parse_llama_guard_response(
            content, p_unsafe if p_unsafe is not None else FALLBACK_MAX_SCORE
        )
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
        lexical_result = self._lexical.score_detailed(text)
        try:
            primary_result = self._primary.score_detailed(text)
        except Exception:
            # Panne du backend primaire (Ollama down/timeout non absorbé) : le
            # lexical, déjà calculé, reste le seul signal — mieux qu'un échec
            # total qui priverait aussi la matrice de repli (pipeline.py) du
            # signal lexical qu'elle aurait pu exploiter. Dégradation
            # journalisée, jamais silencieuse (D4-05).
            logger.warning(
                "Classifieur : backend primaire (Llama Guard/Ollama) en échec — "
                "repli sur le lexique FR seul pour ce message (G1/G2/G3 dégradé)."
            )
            primary_result = ClassifierResult(scores={}, reasoning={})
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
    ollama_url = get_settings().ollama_url
    if not ollama_url:
        return LexicalClassifier()
    try:
        return CombinedClassifier(LlamaGuardClassifier(base_url=ollama_url))
    except Exception:
        return LexicalClassifier()
