"""Orchestration du pipeline garde-fous : étage 1 (regex, séquentiel,
court-circuit) puis étages 2/3 (classifieur + juge + Prompt Shields [+ PII
redaction en sortie]) en vraie concurrence — `ThreadPoolExecutor`, parce que
tous les SDK sous-jacents (ollama, openai, azure-ai-*) sont synchrones.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from velmo.config import get_settings

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

# Matrice de repli par catégorie (conception_chantier2_guardrails.md §Repli &
# robustesse) : G1/G2/G3/G5/G6 fail-closed (le risque de laisser passer est
# pire qu'un refus temporaire) ; G4/G7 fail-open toléré (catégories à
# filtrage, la regex/Luhn de l'étage 1 reste un filet déterministe local même
# si les étages 2/3 tombent).
FAIL_CLOSED_CATEGORIES: frozenset[str] = frozenset(
    {"hate", "violence", "sexual", "out_of_scope", "prompt_injection"}
)
FAIL_OPEN_CATEGORIES: frozenset[str] = frozenset({"pii", "secret_leak"})

# Sources 2/3 (étages parallèles) qui couvrent chaque catégorie. Sert au repli
# **par catégorie** : une catégorie n'applique sa ligne de matrice que si
# TOUTES ses sources présentes ce tour ont échoué (aucune n'a répondu). G6
# (`prompt_injection`) est couvert par le juge ET Prompt Shields — un seul des
# deux qui répond suffit à couvrir la catégorie (Prompt Shields = renfort du
# juge, jamais le seul filet, cf. conception §Repli & robustesse).
CATEGORY_STAGES: dict[str, tuple[str, ...]] = {
    "hate": ("classifier",),
    "violence": ("classifier",),
    "sexual": ("classifier",),
    "out_of_scope": ("judge",),
    "prompt_injection": ("judge", "prompt_shields"),
    "secret_leak": ("judge",),
    "pii": ("pii_redaction",),
}

# Sentinelle d'échec d'un étage 2/3 : distingue une source **configurée mais en
# panne** (timeout/exception → applique la matrice) d'une source **non
# configurée** (feature-flag off → `None`, n'ajoute rien). `None` seul ne
# permettait pas cette distinction (D4-05).
_FAILED = object()

logger = logging.getLogger(__name__)

# Pool partagé, créé une fois : `check_input`/`check_output` sont sur le
# chemin critique de chaque tour agent — recréer/détruire 4 threads à chaque
# appel serait un coût inutile sur le chemin le plus chaud du pipeline.
_EXECUTOR = ThreadPoolExecutor(max_workers=4)


def _fallback_hits(status: dict[str, str]) -> list[Hit]:
    """Repli par catégorie : chaque catégorie dont toutes les sources 2/3
    présentes ont échoué (aucune « ok », au moins une « failed ») applique sa
    ligne de matrice, journalisée `method='fallback'` (D4-01). Une source
    seulement « absent » (non configurée) ne déclenche aucun repli."""
    hits: list[Hit] = []
    for category, sources in CATEGORY_STAGES.items():
        present = [s for s in sources if s in status]
        if not present or any(status[s] == "ok" for s in present):
            continue
        if not any(status[s] == "failed" for s in present):
            continue
        fail_closed = category in FAIL_CLOSED_CATEGORIES
        hits.append(
            Hit(
                category=category,
                method="fallback",
                action="block" if fail_closed else "flag",
                score=None,
                reasoning=(
                    "Étage(s) 2/3 requis indisponible(s) (timeout/erreur) — repli "
                    + ("fail-closed." if fail_closed else "fail-open toléré.")
                ),
            )
        )
    return hits


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
) -> list[Hit]:
    """Exécute le pipeline complet sur `text` (`location` = `"input"`|`"output"`)."""
    hits: list[Hit] = []

    injection_hit = scan_injection(text)
    if injection_hit:
        return [injection_hit]  # seul un vrai `block` (G6) court-circuite tout

    secret_hit = scan_secret_leak(text)
    if secret_hit:
        if location == "input" or secret_hit.action == "block":
            if location == "input":
                # Entrée : toujours un vrai blocage (protège `memory.write` en
                # amont, cf. agent.py `redact_*`-avant-écriture, inchangé par
                # cette tâche) — pas seulement un court-circuit du pipeline.
                secret_hit.action = "block"
            return [secret_hit]
        hits.append(secret_hit)  # sortie + action="filter" : masque, continue vers étages 2/3
    pii_hit = scan_pii(text)
    if pii_hit:
        if location == "input" or pii_hit.action == "block":
            if location == "input":
                pii_hit.action = "block"
            return [pii_hit]
        hits.append(pii_hit)

    # Une résolution de config par tour (au lieu d'une par étage) : passée en
    # `settings=` à `check()`/`scan()`, qui sinon en construiraient chacun une.
    settings = get_settings()
    futures: dict[str, Future[Any]] = {
        "classifier": _EXECUTOR.submit(classifier.score_detailed, text),
        "judge": _EXECUTOR.submit(judge.evaluate, text),
        "prompt_shields": _EXECUTOR.submit(prompt_shields.check, text, settings),
    }
    if location == "output":
        futures["pii_redaction"] = _EXECUTOR.submit(pii_redaction.scan, text, settings)

    results: dict[str, Any] = {}
    for name, future in futures.items():
        try:
            results[name] = future.result(timeout=CALL_TIMEOUT_S)
        except Exception:
            # Occurrence d'une dégradation : journalisée, jamais silencieuse
            # (D4-05). La ligne de matrice s'applique via `_fallback_hits`.
            logger.warning(
                "Garde-fous : étage 2/3 « %s » en échec (timeout/erreur) — "
                "repli par matrice appliqué.",
                name,
            )
            results[name] = _FAILED

    # Statut par source : "ok" (a répondu), "failed" (configurée mais en
    # panne), "absent" (feature-flag non configuré → `None`). Alimente le repli
    # par catégorie (`_fallback_hits`).
    status: dict[str, str] = {}

    classifier_result = results.get("classifier")
    if isinstance(classifier_result, ClassifierResult):
        status["classifier"] = "ok"
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
    else:
        status["classifier"] = "failed"

    judge_scores = results.get("judge")
    if isinstance(judge_scores, dict):
        status["judge"] = "ok"
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
    else:
        status["judge"] = "failed"

    shields_score = results.get("prompt_shields")
    if isinstance(shields_score, (int, float)):
        status["prompt_shields"] = "ok"
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
    elif shields_score is _FAILED:
        status["prompt_shields"] = "failed"
    else:  # None → service non configuré (feature-flag off)
        status["prompt_shields"] = "absent"

    if location == "output":
        pii_result = results.get("pii_redaction")
        if isinstance(pii_result, list):
            status["pii_redaction"] = "ok"
            if pii_result:
                hits.append(
                    Hit(
                        category="pii",
                        method="pii_redaction",
                        action="filter",
                        score=None,
                        # Spans propagés jusqu'à la redaction : un hit `filter`
                        # sans masquage effectif serait une fuite déguisée (D4-03).
                        spans=list(pii_result),
                        reasoning=(
                            f"{len(pii_result)} entité(s) PII détectée(s) par Azure AI Language."
                        ),
                    )
                )
        elif pii_result is _FAILED:
            status["pii_redaction"] = "failed"
        else:  # None → service non configuré (feature-flag off)
            status["pii_redaction"] = "absent"

    hits.extend(_fallback_hits(status))
    return hits
