"""Suite Qualité : cas de support génériques, notés par un juge LLM (DeepEval,
G-Eval) quand un juge Azure est configuré — repli déterministe
(`SubstringScorer`) sinon, même convention que `get_llm`/`get_classifier`/
`get_judge` (EchoLLM/LexicalClassifier/RuleBasedJudge). Voir
conception_chantier3_evaluation_mlops.md §Suite Qualité.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol

from velmo.mlops.results import CaseResult

EVAL_PATH = Path(__file__).resolve().parents[4] / "eval" / "quality_cases.jsonl"


class QualityScorer(Protocol):
    def score(self, question: str, answer: str, expected_substring: str) -> float: ...


class SubstringScorer:
    """Repli hors-ligne, déterministe : proxy binaire (0.0/1.0) sur la
    présence du substring attendu — pas un jugement de qualité nuancé, mais un
    signal exploitable sans dépendance cloud."""

    def score(self, question: str, answer: str, expected_substring: str) -> float:
        return 1.0 if expected_substring in answer else 0.0


class DeepEvalScorer:
    """Juge Azure pinné (id + version d'API), rubrique G-Eval versionnée —
    voir conception_chantier3_evaluation_mlops.md §Pourquoi DeepEval, mais
    cadré. `temperature=0` côté juge (déterminisme, cf. §M4).

    Utilise le déploiement **async** (`azure_openai_async_*`, Chantier 1 Task 9),
    partagé avec l'extracteur mémoire — les deux usages sont asynchrones/
    best-effort, contrairement au juge garde-fous (déploiement `guard` dédié,
    chemin bloquant, Chantier 2 Task 9). Ne jamais lire les champs `guard_*`
    ici : ce serait recréer le couplage de quota que la séparation des
    déploiements (Q1, session de grilling) visait à éliminer.

    Le modèle Azure est construit **une fois** ici (`AzureOpenAIModel`,
    https://deepeval.com/integrations/models/azure-openai) et réutilisé pour
    chaque `score()` — passer `model=self._deployment` (une simple string) à
    `GEval` ne suffit pas à router vers Azure : DeepEval a besoin d'une
    instance de modèle configurée avec `endpoint`/`api_key`/`api_version`,
    sinon `endpoint`/`api_key` ci-dessous seraient validés (`require`) mais
    jamais utilisés pour l'appel réel.
    """

    def __init__(self) -> None:
        from deepeval.models import AzureOpenAIModel

        from velmo.config import get_settings, require

        settings = get_settings()
        endpoint = require(settings.azure_openai_async_endpoint, "AZURE_OPENAI_ASYNC_ENDPOINT")
        api_key = require(settings.azure_openai_async_api_key, "AZURE_OPENAI_ASYNC_API_KEY")
        self._model = AzureOpenAIModel(
            model=settings.azure_openai_async_deployment,
            deployment_name=settings.azure_openai_async_deployment,
            api_key=api_key,
            api_version=settings.azure_openai_async_api_version,
            base_url=endpoint,
            temperature=0,  # déterminisme du juge, cf. §M4
        )

    def score(self, question: str, answer: str, expected_substring: str) -> float:
        from deepeval.metrics import GEval
        from deepeval.test_case import LLMTestCase, SingleTurnParams

        metric = GEval(
            name="Pertinence support Velmo",
            criteria=(
                "La réponse répond-elle précisément à la question du client de "
                "support, en restant cohérente avec l'information attendue ?"
            ),
            evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
            model=self._model,
        )
        test_case = LLMTestCase(input=question, actual_output=answer)
        metric.measure(test_case)
        if metric.score is None:
            raise RuntimeError("GEval.measure() n'a pas renseigné de score")
        return float(metric.score)


def get_quality_scorer() -> QualityScorer:
    """DeepEval si `AZURE_OPENAI_ASYNC_ENDPOINT`/`AZURE_OPENAI_ASYNC_API_KEY`
    sont définis (déploiement async, partagé avec l'extracteur mémoire —
    jamais le déploiement `guard` du juge garde-fous), sinon `SubstringScorer`
    — même contrat de repli gracieux que le reste du codebase."""
    from velmo.config import get_settings

    settings = get_settings()
    if settings.azure_openai_async_endpoint and settings.azure_openai_async_api_key:
        try:
            return DeepEvalScorer()
        except Exception:
            return SubstringScorer()
    return SubstringScorer()


def _load_cases() -> list[dict[str, Any]]:
    text = EVAL_PATH.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def run_quality_suite(agent: Any, db_url: str | None = None) -> list[CaseResult]:
    """`agent` : objet exposant `.respond(user_id, message) -> str` (protocole
    `Evaluable`, cf. `mlops/__init__.py`). `db_url` non utilisé directement ici
    (l'agent porte déjà sa propre mémoire/session) — gardé pour homogénéité de
    signature avec les deux autres suites."""
    scorer = get_quality_scorer()
    results: list[CaseResult] = []
    for case in _load_cases():
        start = time.monotonic()
        try:
            answer = agent.respond(case["user_id"], case["question"])
            score = scorer.score(case["question"], answer, case["expected_substring"])
            results.append(
                CaseResult(
                    case_id=case["id"], suite="quality", passed=score >= 0.5,
                    score=score, latency_ms=(time.monotonic() - start) * 1000,
                )
            )
        except Exception:
            results.append(
                CaseResult(
                    case_id=case["id"], suite="quality", passed=False, score=0.0,
                    latency_ms=(time.monotonic() - start) * 1000, error_kind="infra",
                )
            )
    return results
