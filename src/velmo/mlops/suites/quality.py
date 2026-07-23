"""Suite Qualité : cas de support génériques, notés par un juge LLM (DeepEval,
G-Eval) quand un juge Claude (Azure AI Foundry) est configuré — repli
déterministe (`SubstringScorer`) sinon, même convention que `get_llm`/
`get_classifier`/`get_judge` (EchoLLM/LexicalClassifier/RuleBasedJudge). Voir
conception_chantier3_evaluation_mlops.md §Suite Qualité.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

from velmo.mlops.results import CaseResult, CaseStepEvent

EVAL_PATH = Path(__file__).resolve().parents[4] / "eval" / "quality_cases.jsonl"


class QualityScorer(Protocol):
    def score(self, question: str, answer: str, expected_substring: str) -> float: ...


class SubstringScorer:
    """Repli hors-ligne, déterministe : proxy binaire (0.0/1.0) sur la
    présence du substring attendu — pas un jugement de qualité nuancé, mais un
    signal exploitable sans dépendance cloud."""

    def score(self, question: str, answer: str, expected_substring: str) -> float:
        return 1.0 if expected_substring in answer else 0.0


def _foundry_anthropic_model_cls() -> Any:
    """Sous-classe de `deepeval.models.AnthropicModel` pointée sur Azure AI
    Foundry (`AnthropicFoundry`) plutôt que l'API Anthropic directe — import
    différé (dépendance optionnelle `deepeval`/`anthropic`, même convention
    que le reste du module). `AnthropicModel._build_client` instancie
    `anthropic.Anthropic`/`AsyncAnthropic` en dur (vérifié dans le package
    installé) : impossible d'y injecter `AnthropicFoundry` autrement qu'en
    surchargeant `load_model`. `base_url` passé au constructeur n'est pas dans
    l'alias_map de DeepEval, donc survit tel quel jusqu'à
    `AnthropicFoundry(api_key=..., base_url=...)`.
    """
    from deepeval.models import AnthropicModel

    # deepeval n'expose pas de types stricts (`AnthropicModel`, `_build_client`
    # non annotés) — `type: ignore[no-untyped-call]` ciblé plutôt que relâcher
    # `mypy strict` pour tout le module.
    class _FoundryAnthropicModel(AnthropicModel):  # type: ignore[no-untyped-call]
        def load_model(self, async_mode: bool = False) -> Any:
            from anthropic import AnthropicFoundry, AsyncAnthropicFoundry

            client_cls = AsyncAnthropicFoundry if async_mode else AnthropicFoundry
            return self._build_client(client_cls)  # type: ignore[no-untyped-call]

    return _FoundryAnthropicModel


class DeepEvalScorer:
    """Juge Claude (`claude-opus-4-5`) via Azure AI Foundry, rubrique G-Eval
    versionnée — voir conception_chantier3_evaluation_mlops.md §Pourquoi
    DeepEval, mais cadré. `temperature=0` côté juge (déterminisme, cf. §M4).

    Utilise le déploiement **async** (`anthropic_*`, Chantier 1 Task 9),
    partagé avec l'extracteur mémoire — les deux usages sont asynchrones/
    best-effort, contrairement au juge garde-fous (déploiement `guard` dédié
    Azure OpenAI, chemin bloquant, Chantier 2 Task 9). Ne jamais lire les
    champs `guard_*` ici : ce serait recréer le couplage de quota que la
    séparation des déploiements (Q1, session de grilling) visait à éliminer.

    Voir `_foundry_anthropic_model_cls()` : `deepeval.models.AnthropicModel`
    instancie `anthropic.Anthropic` en dur, d'où la sous-classe qui pointe
    `AnthropicFoundry` à la place.
    """

    def __init__(self) -> None:
        from velmo.config import get_settings, require

        settings = get_settings()
        endpoint = require(settings.anthropic_foundry_endpoint, "ANTHROPIC_FOUNDRY_ENDPOINT")
        api_key = require(settings.anthropic_api_key, "ANTHROPIC_API_KEY")
        self._model = _foundry_anthropic_model_cls()(
            model=settings.anthropic_async_model,
            api_key=api_key,
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
    """DeepEval si `ANTHROPIC_FOUNDRY_ENDPOINT`/`ANTHROPIC_API_KEY` sont
    définis (déploiement async, partagé avec l'extracteur mémoire — jamais le
    déploiement `guard` du juge garde-fous), sinon `SubstringScorer` — même
    contrat de repli gracieux que le reste du codebase."""
    from velmo.config import get_settings

    settings = get_settings()
    if settings.anthropic_foundry_endpoint and settings.anthropic_api_key:
        try:
            return DeepEvalScorer()
        except Exception:
            return SubstringScorer()
    return SubstringScorer()


def _load_cases() -> list[dict[str, Any]]:
    text = EVAL_PATH.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def run_quality_suite_steps(agent: Any, db_url: str | None = None) -> Iterator[CaseStepEvent]:
    """Version générateur de `run_quality_suite` — diffuse un `CaseStepEvent`
    `"start"` avant chaque cas puis `"done"` une fois son `CaseResult` connu
    (même rôle que `run_memory_suite_steps`), pour que `run_eval_steps`
    streame la progression cas par cas."""
    scorer = get_quality_scorer()
    for case in _load_cases():
        yield CaseStepEvent("start", case["id"])
        start = time.monotonic()
        try:
            answer = agent.respond(case["user_id"], case["question"])
            score = scorer.score(case["question"], answer, case["expected_substring"])
            result = CaseResult(
                case_id=case["id"],
                suite="quality",
                passed=score >= 0.5,
                score=score,
                latency_ms=(time.monotonic() - start) * 1000,
            )
        except Exception:
            result = CaseResult(
                case_id=case["id"],
                suite="quality",
                passed=False,
                score=0.0,
                latency_ms=(time.monotonic() - start) * 1000,
                error_kind="infra",
            )
        yield CaseStepEvent("done", case["id"], result)


def run_quality_suite(agent: Any, db_url: str | None = None) -> list[CaseResult]:
    """`agent` : objet exposant `.respond(user_id, message) -> str` (protocole
    `Evaluable`, cf. `mlops/__init__.py`). `db_url` non utilisé directement ici
    (l'agent porte déjà sa propre mémoire/session) — gardé pour homogénéité de
    signature avec les deux autres suites."""
    results: list[CaseResult] = []
    for event in run_quality_suite_steps(agent, db_url=db_url):
        if event.kind == "done":
            assert event.result is not None
            results.append(event.result)
    return results
