"""Calibre BLOCK_THRESHOLD/FLAG_THRESHOLD/ESCALATE_THRESHOLD sur
eval/guardrail_cases.jsonl à partir des scores réels (Ollama + Azure).

Usage : `uv run python eval/calibrate_thresholds.py`

Nécessite OLLAMA_URL et AZURE_OPENAI_GUARD_ENDPOINT/AZURE_OPENAI_GUARD_API_KEY dans
l'environnement pour utiliser les backends réels (sinon repli automatique sur
LexicalClassifier/RuleBasedJudge — les valeurs affichées ne seront alors pas
représentatives). Script one-shot, hors CI : affiche des seuils candidats à
recopier manuellement dans pipeline.py après revue humaine (pas
d'auto-tuning au runtime, cf.
docs/superpowers/specs/2026-07-16-gradation-scores-guardrails-design.md §4).
"""

from __future__ import annotations

import json
from pathlib import Path

from velmo.guardrails.classifier import ModerationClassifier, get_classifier
from velmo.guardrails.judge import Judge, get_judge

EVAL_PATH = Path(__file__).resolve().parent / "guardrail_cases.jsonl"


def _load_cases() -> list[dict[str, object]]:
    text = EVAL_PATH.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _max_score(
    case: dict[str, object], classifier: ModerationClassifier, judge: Judge
) -> float:
    """Score maximal tous signaux confondus pour un cas (simplifié par
    rapport à pipeline.run() : pas de court-circuit étage 1, on veut voir le
    score brut classifieur/juge même sur un cas qu'une regex bloquerait déjà,
    pour calibrer les seuils sur le signal gradué lui-même)."""
    message = str(case["message"])
    classifier_scores = classifier.score(message)
    judge_scores = judge.evaluate(message)
    values = list(classifier_scores.values())
    values += [v for v in judge_scores.values() if isinstance(v, float)]
    return max(values, default=0.0)


def calibrate() -> None:
    classifier = get_classifier()
    judge = get_judge()
    cases = _load_cases()
    scored = [(case, _max_score(case, classifier, judge)) for case in cases]

    blocks = [(str(c["id"]), s) for c, s in scored if c["expected_action"] == "block"]
    allows = [(str(c["id"]), s) for c, s in scored if c["expected_action"] == "allow"]
    escalates = [(str(c["id"]), s) for c, s in scored if c.get("expected_escalate")]

    print("Scores 'block' attendus :", sorted(blocks, key=lambda x: x[1]))
    print("Scores 'allow' attendus :", sorted(allows, key=lambda x: x[1]))
    print("Scores 'expected_escalate' :", sorted(escalates, key=lambda x: x[1]))

    max_allow = max((s for _, s in allows), default=0.0)
    min_block = min((s for _, s in blocks), default=1.0)
    min_escalate = min((s for _, s in escalates), default=None)

    block_threshold = round(max_allow + 0.01, 2)
    flag_threshold = round(block_threshold * 0.6, 2)
    escalate_threshold = min_escalate if min_escalate is not None else 0.9

    print("\nSeuils candidats :")
    print(f"  FLAG_THRESHOLD = {flag_threshold}")
    print(f"  BLOCK_THRESHOLD = {block_threshold}")
    print(f"  ESCALATE_THRESHOLD = {escalate_threshold}")

    if min_block <= max_allow:
        print(
            "\nATTENTION : un cas 'block' a un score <= au plus haut 'allow' "
            f"({min_block} <= {max_allow}) — aucun seuil unique ne sépare "
            "proprement les deux, revoir les cas avant de recopier ces valeurs."
        )
    if min_escalate is None:
        print(
            "\nPas de cas 'expected_escalate' dans le dataset — "
            "ESCALATE_THRESHOLD reste à sa valeur de départ (0.9), non calibrée."
        )


if __name__ == "__main__":
    calibrate()
