"""Orchestration du pipeline garde-fous : étage 1 (regex, séquentiel,
court-circuit) puis étages 2/3 (classifieur + juge + Prompt Shields [+ PII
redaction en sortie]) en vraie concurrence — `ThreadPoolExecutor`, parce que
tous les SDK sous-jacents (detoxify, openai, azure-ai-*) sont synchrones.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Union

from . import pii_redaction, prompt_shields
from .classifier import ModerationClassifier
from .judge import Judge
from .patterns import Hit, scan_injection, scan_pii, scan_secret_leak

__all__ = ["Hit", "run"]

BLOCK_THRESHOLD = 0.7
FLAG_THRESHOLD = 0.4
CALL_TIMEOUT_S = 3.0


def _level(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= BLOCK_THRESHOLD:
        return "block"
    if score >= FLAG_THRESHOLD:
        return "flag"
    return None


def run(
    text: str,
    *,
    location: str,
    classifier: ModerationClassifier,
    judge: Judge,
    agent_response: str | None = None,
) -> list[Hit]:
    """Exécute le pipeline complet sur `text` (`location` = `"input"`|`"output"`)."""
    hits: list[Hit] = []

    injection_hit = scan_injection(text)
    if injection_hit:
        return [injection_hit]  # court-circuit : rien de plus à évaluer
    secret_hit = scan_secret_leak(text)
    if secret_hit:
        return [secret_hit]
    if location == "output":
        pii_hit = scan_pii(text)
        if pii_hit:
            hits.append(pii_hit)  # "filter" au sens conception : le reste continue

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures: dict[str, Future[Any]] = {
            "classifier": pool.submit(classifier.score, text),
            "judge": pool.submit(judge.evaluate, text, agent_response),
            "prompt_shields": pool.submit(prompt_shields.check, text),
        }
        if location == "output":
            futures["pii_redaction"] = pool.submit(pii_redaction.scan, text)

        results: dict[str, Union[dict[str, float], float, list[tuple[int, int]], None]] = {}
        for name, future in futures.items():
            try:
                results[name] = future.result(timeout=CALL_TIMEOUT_S)
            except Exception:
                results[name] = None

    any_stage_2_3_responded = False

    classifier_scores = results.get("classifier")
    if isinstance(classifier_scores, dict):
        any_stage_2_3_responded = True
        for category in ("hate", "violence", "sexual"):
            level = _level(classifier_scores.get(category))
            if level:
                hits.append(
                    Hit(
                        category=category,
                        method="classifier",
                        action=level,
                        score=classifier_scores[category],
                    )
                )

    judge_scores = results.get("judge")
    if isinstance(judge_scores, dict):
        any_stage_2_3_responded = True
        level = _level(judge_scores.get("hors_role"))
        if level:
            hits.append(
                Hit(category="out_of_scope", method="llm_judge", action=level,
                    score=judge_scores["hors_role"])
            )
        level = _level(judge_scores.get("manipulation"))
        if level:
            hits.append(
                Hit(category="prompt_injection", method="llm_judge", action=level,
                    score=judge_scores["manipulation"])
            )
        level = _level(judge_scores.get("secret_interne"))
        if level:
            hits.append(
                Hit(category="secret_leak", method="llm_judge", action=level,
                    score=judge_scores["secret_interne"])
            )

    shields_score = results.get("prompt_shields")
    if isinstance(shields_score, (int, float)):
        any_stage_2_3_responded = True
        level = _level(float(shields_score))
        if level:
            hits.append(
                Hit(category="prompt_injection", method="prompt_shields", action=level,
                    score=float(shields_score))
            )

    if location == "output":
        pii_spans = results.get("pii_redaction")
        if isinstance(pii_spans, list) and pii_spans:
            any_stage_2_3_responded = True
            hits.append(Hit(category="pii", method="pii_redaction", action="block", score=None))

    if not hits and not any_stage_2_3_responded:
        # Tous les étages 2/3 ont échoué/timeout et l'étage 1 n'a rien
        # détecté : zone grise plutôt qu'un passage silencieux ou un blocage
        # injustifié sur simple indisponibilité réseau.
        hits.append(Hit(category="availability", method="timeout", action="flag", score=None))

    return hits
