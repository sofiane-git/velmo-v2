"""Orchestration du pipeline garde-fous : étage 1 (regex, séquentiel,
court-circuit) puis étages 2/3 (classifieur + juge + Prompt Shields [+ PII
redaction en sortie]) en vraie concurrence — `ThreadPoolExecutor`, parce que
tous les SDK sous-jacents (detoxify, openai, azure-ai-*) sont synchrones.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from . import pii_redaction, prompt_shields
from ._scoring import FALLBACK_MAX_SCORE
from ._timeouts import CALL_TIMEOUT_S
from .classifier import ClassifierResult, ModerationClassifier
from .judge import Judge
from .patterns import Hit, scan_injection, scan_pii, scan_secret_leak

__all__ = ["Hit", "run"]

BLOCK_THRESHOLD = 0.7
FLAG_THRESHOLD = 0.4
# Valeur de départ, à recalibrer sur eval/guardrail_cases.jsonl une fois des
# cas expected_escalate labellisés disponibles (eval/calibrate_thresholds.py,
# cf. docs/superpowers/specs/2026-07-16-gradation-scores-guardrails-design.md
# §4). Doit toujours rester strictement au-dessus de FALLBACK_MAX_SCORE : un
# repli hors-ligne sans confiance calibrée ne doit jamais atteindre ce palier
# seul.
ESCALATE_THRESHOLD = 0.9
assert FALLBACK_MAX_SCORE < ESCALATE_THRESHOLD

# Clé du dict renvoyé par Judge.evaluate() -> catégorie G1-G7 correspondante.
JUDGE_KEY_TO_CATEGORY = {
    "hors_role": "out_of_scope",
    "manipulation": "prompt_injection",
    "secret_interne": "secret_leak",
}

# Pool partagé, créé une fois : `check_input`/`check_output` sont sur le
# chemin critique de chaque tour agent — recréer/détruire 4 threads à chaque
# appel serait un coût inutile sur le chemin le plus chaud du pipeline.
_EXECUTOR = ThreadPoolExecutor(max_workers=4)


def _level(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= ESCALATE_THRESHOLD:
        return "block_escalate"
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
    pii_hit = scan_pii(text)
    if pii_hit:
        return [pii_hit]  # court-circuit : la donnée sensible ne part pas vers classifieur/juge

    futures: dict[str, Future[Any]] = {
        "classifier": _EXECUTOR.submit(classifier.score_detailed, text),
        "judge": _EXECUTOR.submit(judge.evaluate, text, agent_response),
        "prompt_shields": _EXECUTOR.submit(prompt_shields.check, text),
    }
    if location == "output":
        futures["pii_redaction"] = _EXECUTOR.submit(pii_redaction.scan, text)

    results: dict[str, Any] = {}
    for name, future in futures.items():
        try:
            results[name] = future.result(timeout=CALL_TIMEOUT_S)
        except Exception:
            results[name] = None

    any_stage_2_3_responded = False

    classifier_result = results.get("classifier")
    if isinstance(classifier_result, ClassifierResult):
        any_stage_2_3_responded = True
        for category in ("hate", "violence", "sexual"):
            level = _level(classifier_result.scores.get(category))
            if level:
                hits.append(
                    Hit(
                        category=category,
                        method="classifier",
                        action=level,
                        score=classifier_result.scores[category],
                        reasoning=classifier_result.reasoning.get(category),
                    )
                )

    judge_scores = results.get("judge")
    if isinstance(judge_scores, dict):
        any_stage_2_3_responded = True
        judge_reasoning = judge_scores.get("reasoning")
        reasoning_text = judge_reasoning if judge_reasoning else None
        for key, category in JUDGE_KEY_TO_CATEGORY.items():
            level = _level(judge_scores.get(key))
            if level:
                hits.append(
                    Hit(
                        category=category,
                        method="llm_judge",
                        action=level,
                        score=judge_scores[key],
                        reasoning=reasoning_text,
                    )
                )

    shields_score = results.get("prompt_shields")
    if isinstance(shields_score, (int, float)):
        any_stage_2_3_responded = True
        shields_score = float(shields_score)
        level = _level(shields_score)
        if level:
            hits.append(
                Hit(
                    category="prompt_injection",
                    method="prompt_shields",
                    action=level,
                    score=shields_score,
                    reasoning="Azure Prompt Shields a détecté une tentative d'injection.",
                )
            )

    if location == "output":
        pii_spans = results.get("pii_redaction")
        if isinstance(pii_spans, list) and pii_spans:
            any_stage_2_3_responded = True
            hits.append(
                Hit(
                    category="pii",
                    method="pii_redaction",
                    action="block",
                    score=None,
                    reasoning=f"{len(pii_spans)} entité(s) PII détectée(s) par Azure AI Language.",
                )
            )

    if not hits and not any_stage_2_3_responded:
        # Tous les étages 2/3 ont échoué/timeout et l'étage 1 n'a rien
        # détecté : zone grise plutôt qu'un passage silencieux ou un blocage
        # injustifié sur simple indisponibilité réseau.
        hits.append(Hit(category="availability", method="timeout", action="flag", score=None))

    return hits
