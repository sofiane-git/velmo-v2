"""Suite Mémoire : rejoue `eval/memory_cases.jsonl` contre `MemoryManager`
(API publique du Chantier 1 uniquement — `write`, `read`, `forget`, `inspect`).
Chaque cas est pass/fail binaire déterministe (conception_chantier3_evaluation_mlops.md
§Les trois suites d'évaluation). 1 retry sur un cas en échec (`with_retry`,
Task 2 Step 1) ; latence décomposée par composant via `sink` (Task 5).
"""

from __future__ import annotations

import functools
import json
import time
from pathlib import Path
from typing import Any, cast

from velmo.config import get_settings
from velmo.memory import MemoryManager
from velmo.memory.extractor import FactExtractor, get_extractor
from velmo.mlops.observability import InstrumentedExtractor, InstrumentedLLM, NullSink, ObservabilitySink
from velmo.mlops.results import CaseResult, with_retry

EVAL_PATH = Path(__file__).resolve().parents[4] / "eval" / "memory_cases.jsonl"


def _load_cases() -> list[dict[str, Any]]:
    text = EVAL_PATH.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _instrumented_extractor(sink: ObservabilitySink) -> FactExtractor:
    settings = get_settings()
    return InstrumentedExtractor(
        get_extractor(), sink, "memory_extractor", settings.anthropic_async_model
    )


def _build_manager(db_url: str | None, sink: ObservabilitySink, **overrides: Any) -> MemoryManager:
    """Assemble un `MemoryManager` avec extracteur/LLM de résumé instrumentés
    (composition pure, cf. Task 5 — aucun changement de `MemoryManager`)."""
    settings = get_settings()
    from velmo.llm import get_llm

    return MemoryManager(
        db_url=db_url,
        extractor=_instrumented_extractor(sink),
        llm=InstrumentedLLM(get_llm(), sink, "memory_summary", settings.azure_ai_inference_model),
        **overrides,
    )


def _replay_turns(manager: MemoryManager, user_id: str, turns: list[dict[str, str]]) -> None:
    """Rejoue le scénario scripté tour par tour — écrit les messages tels que
    fournis par la fixture (pas de génération LLM : on teste la mémoire, pas
    la qualité de réponse, cf. Suite Qualité pour ça)."""
    pending_user: str | None = None
    for turn in turns:
        if turn["role"] == "user":
            pending_user = turn["content"]
        elif turn["role"] == "assistant" and pending_user is not None:
            manager.write(user_id, pending_user, turn["content"])
            pending_user = None


def _check_no_cross_leak(
    own_substring: str, other_case_substrings: list[str], rendered_context: str
) -> bool:
    """Vrai si aucun identifiant appartenant à un AUTRE utilisateur (`R3`)
    n'apparaît dans le contexte rendu de l'utilisateur courant. `own_substring`
    documente l'appel : exclure son propre identifiant est la responsabilité
    de l'appelant (`_run_r3_group` ne passe que les substrings des AUTRES cas)
    — une valeur qui coïnciderait avec `own_substring` dans
    `other_case_substrings` doit rester détectée comme fuite (collision de
    valeur entre deux utilisateurs), jamais ignorée par égalité de chaîne."""
    for other in other_case_substrings:
        if other in rendered_context:
            return False
    return True


def _run_recall_or_persistence_case(
    case: dict[str, Any], db_url: str | None, sink: ObservabilitySink
) -> CaseResult:
    start = time.monotonic()
    try:
        manager = _build_manager(db_url, sink, token_budget=8000)
        _replay_turns(manager, case["user_id"], case["turns"])
        rendered = manager.read(case["user_id"], case["evaluation"]["question"]).render()
        passed = case["evaluation"]["expected_substring"] in rendered
        return CaseResult(
            case_id=case["id"], suite="memory", passed=passed,
            score=1.0 if passed else 0.0, latency_ms=(time.monotonic() - start) * 1000,
        )
    except Exception:
        return CaseResult(
            case_id=case["id"], suite="memory", passed=False, score=0.0,
            latency_ms=(time.monotonic() - start) * 1000, error_kind="infra",
        )


def _run_forget_case(case: dict[str, Any], db_url: str | None, sink: ObservabilitySink) -> CaseResult:
    start = time.monotonic()
    try:
        manager = _build_manager(db_url, sink, token_budget=8000)
        # Ne rejoue que les tours avant la demande d'oubli (le "forget" est un
        # appel explicite de l'API, pas une intention détectée par un NLU dans
        # ce test déterministe — cf. tests/acceptance/test_memory.py, même
        # pattern).
        seed_turns = case["turns"][:2]
        _replay_turns(manager, case["user_id"], seed_turns)
        manager.forget(case["user_id"], case["evaluation"]["target"])
        rendered = manager.read(case["user_id"], case["evaluation"]["question"]).render()
        passed = case["evaluation"]["forbidden_substring"] not in rendered
        return CaseResult(
            case_id=case["id"], suite="memory", passed=passed,
            score=1.0 if passed else 0.0, latency_ms=(time.monotonic() - start) * 1000,
        )
    except Exception:
        return CaseResult(
            case_id=case["id"], suite="memory", passed=False, score=0.0,
            latency_ms=(time.monotonic() - start) * 1000, error_kind="infra",
        )


def _run_inspect_case(case: dict[str, Any], db_url: str | None, sink: ObservabilitySink) -> CaseResult:
    start = time.monotonic()
    try:
        manager = _build_manager(db_url, sink, token_budget=8000)
        _replay_turns(manager, case["user_id"], case["turns"])
        inspection = manager.inspect(case["user_id"])
        evaluation = case["evaluation"]
        facts = cast(list[dict[str, Any]], inspection["facts"])
        matching = [
            f for f in facts
            if f["key"] == evaluation["expected_key"] and evaluation["expected_substring"] in f["value"]
        ]
        passed = len(matching) > 0
        if passed and evaluation.get("expect_source"):
            passed = matching[0]["source_thread_id"] is not None
        return CaseResult(
            case_id=case["id"], suite="memory", passed=passed,
            score=1.0 if passed else 0.0, latency_ms=(time.monotonic() - start) * 1000,
        )
    except Exception:
        return CaseResult(
            case_id=case["id"], suite="memory", passed=False, score=0.0,
            latency_ms=(time.monotonic() - start) * 1000, error_kind="infra",
        )


def _run_r3_group(
    cases: list[dict[str, Any]], db_url: str | None, sink: ObservabilitySink
) -> list[CaseResult]:
    """Cas R3 (isolation) : rejoués dans un `MemoryManager` PARTAGÉ (même
    base) pour que la fuite inter-clients soit un vrai risque testé — pas un
    recall indépendant par instance isolée, qui ne prouverait rien sur R3. Pas
    de retry ici : un retry par cas casserait le partage de `manager` entre
    les deux users R3 (isolation = propriété du couple, pas d'un cas seul)."""
    start = time.monotonic()
    manager = _build_manager(db_url, sink, token_budget=8000)
    for case in cases:
        _replay_turns(manager, case["user_id"], case["turns"])

    results: list[CaseResult] = []
    for case in cases:
        try:
            rendered = manager.read(case["user_id"], case["evaluation"]["question"]).render()
            own = case["evaluation"]["expected_substring"]
            others = [
                c["evaluation"]["expected_substring"] for c in cases if c["id"] != case["id"]
            ]
            recall_ok = own in rendered
            no_leak = _check_no_cross_leak(own, others, rendered)
            passed = recall_ok and no_leak
            results.append(
                CaseResult(
                    case_id=case["id"], suite="memory", passed=passed,
                    score=1.0 if passed else 0.0, latency_ms=(time.monotonic() - start) * 1000,
                )
            )
        except Exception:
            results.append(
                CaseResult(
                    case_id=case["id"], suite="memory", passed=False, score=0.0,
                    latency_ms=(time.monotonic() - start) * 1000, error_kind="infra",
                )
            )
    return results


def run_memory_suite(
    db_url: str | None = None, sink: ObservabilitySink | None = None
) -> list[CaseResult]:
    """Rejoue tous les cas de `eval/memory_cases.jsonl`. `db_url=None` est
    transmis tel quel à chaque `MemoryManager` : c'est `make_memory_engine`
    (Chantier 1) qui résout alors `Settings.db_url` (Postgres si joignable,
    sinon repli SQLite fichier) — cette suite ne doit **pas** court-circuiter
    cette résolution en forçant un `:memory:` par défaut, qui ignorerait
    silencieusement la base configurée en prod/CI réelle. Un appelant qui veut
    une isolation totale (tests) passe explicitement `db_url="sqlite:///:memory:"`.
    `sink=None` retombe sur `NullSink` (pas d'instrumentation, comportement
    historique).
    """
    sink = sink or NullSink()
    cases = _load_cases()
    results: list[CaseResult] = []

    r3_cases = [c for c in cases if c["tag"] == "R3"]
    other_cases = [c for c in cases if c["tag"] != "R3"]

    for case in other_cases:
        evaluation_type = case["evaluation"]["type"]
        # R4 exige un budget de tokens volontairement bas pour forcer la
        # compression sur ce cas précis (`requires_summarization`) — les
        # autres cas gardent le budget par défaut (8000, cf. Chantier 1) pour
        # ne jamais déclencher de résumé sur des conversations courtes.
        if case["evaluation"].get("requires_summarization"):
            results.append(
                with_retry(functools.partial(_run_recall_with_small_budget, case, db_url, sink))
            )
            continue
        if evaluation_type in ("recall", "persistence"):
            results.append(
                with_retry(functools.partial(_run_recall_or_persistence_case, case, db_url, sink))
            )
        elif evaluation_type == "forget":
            results.append(with_retry(functools.partial(_run_forget_case, case, db_url, sink)))
        elif evaluation_type == "inspect":
            results.append(with_retry(functools.partial(_run_inspect_case, case, db_url, sink)))

    results.extend(_run_r3_group(r3_cases, db_url, sink))
    return results


def _run_recall_with_small_budget(
    case: dict[str, Any], db_url: str | None, sink: ObservabilitySink
) -> CaseResult:
    start = time.monotonic()
    try:
        # Budget délibérément bas : force la compression sur ~16 messages
        # (cf. fixture R4-budget-dossier) sans affecter les autres cas.
        manager = _build_manager(db_url, sink, token_budget=150, keep_last_n_turns=2)
        _replay_turns(manager, case["user_id"], case["turns"])
        rendered = manager.read(case["user_id"], case["evaluation"]["question"]).render()
        passed = case["evaluation"]["expected_substring"] in rendered
        return CaseResult(
            case_id=case["id"], suite="memory", passed=passed,
            score=1.0 if passed else 0.0, latency_ms=(time.monotonic() - start) * 1000,
        )
    except Exception:
        return CaseResult(
            case_id=case["id"], suite="memory", passed=False, score=0.0,
            latency_ms=(time.monotonic() - start) * 1000, error_kind="infra",
        )
