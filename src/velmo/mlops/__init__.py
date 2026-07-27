"""Évaluation et MLOps de l'agent Velmo : suites, note globale, seuil, rapport.

Surface publique stable consommée par la suite d'acceptance et la CI.
"""

from __future__ import annotations

import logging
import statistics
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, cast

from sqlalchemy.orm import Session, sessionmaker

from velmo.config import get_settings
from velmo.mlops.budget import ComponentLatency, component_latency_report
from velmo.mlops.db import AgentVersion, EvalCaseResult, EvalRun, make_mlops_engine
from velmo.mlops.observability import CostAccumulatingSink, NullSink, ObservabilitySink
from velmo.mlops.results import CaseResult
from velmo.mlops.stats import non_regression_ok
from velmo.mlops.suites.guardrails import guardrails_confusion_matrix, run_guardrails_suite_steps
from velmo.mlops.suites.memory import run_memory_suite_steps
from velmo.mlops.suites.quality import run_quality_suite_steps
from velmo.mlops.suites.tools import run_tools_suite_steps, tools_scores
from velmo.mlops.versioning import compute_version_hashes, current_git_commit, current_git_tag

logger = logging.getLogger(__name__)

# Seuils SLO non-fonctionnels et plancher du gate : source unique en config
# (`Settings.gate_*`, conception §Seuils — « chiffres versionnés dans un
# fichier de config, donc hashés dans la version ») ; lus à l'exécution et
# inclus dans `compute_version_hashes()` (gate_config_hash, audit D8-05).
# Surcharge en test : `monkeypatch.setenv("GATE_LATENCY_P95_CEILING_MS", ...)`
# — voir `test_run_eval_blocks_when_latency_slo_exceeded`.


class Evaluable(Protocol):
    """Agent évaluable : expose mémoire, garde-fous et une réponse."""

    def respond(self, user_id: str, message: str) -> str: ...


@dataclass(frozen=True)
class Scores:
    """Notes d'une exécution d'évaluation."""

    memory: float
    guardrails: float
    quality: float
    global_: float
    block_rate: float
    false_positive_rate: float
    latency_ms: float
    cost: float
    # Mesures dotées d'un propriétaire dans le contrat de rapport (audit
    # O-01/O-02/O-04). Toutes **hors gate** : elles informent la calibration et
    # l'imputation d'un dépassement, elles ne bloquent pas une livraison.
    # `None` = non mesuré sur ce run — volontairement distinct de `0.0`, qu'un
    # lecteur interpréterait comme « mesuré et parfait » (voir `report.py`).
    latency_by_component: list[ComponentLatency] = field(default_factory=list)
    judge_shadow_divergence_rate: float | None = None
    extractor_precision: float | None = None
    extractor_recall: float | None = None
    # Couche d'actions : `tools` gate (cas déterministes), `tool_selection_accuracy`
    # est du reporting. `None` = suite non exécutée sur ce run.
    tools: float | None = None
    tool_selection_accuracy: float | None = None


@dataclass(frozen=True)
class GateEvent:
    """Un pas de `run_eval_steps` : une suite qui démarre
    (`stage="suite_start"`), un cas qui démarre (`stage="case_start"`), un cas
    terminé (`stage="case_done"`), une suite qui vient de finir
    (`stage="suite_done"`), ou l'agrégat final (`stage="final"`). `payload`
    reste un `dict` JSON-serialisable (pas de `Scores`/`CaseResult` imbriqué)
    — c'est ce que l'API `/mlops/gate/run` sérialise directement en SSE."""

    stage: Literal["suite_start", "case_start", "case_done", "suite_done", "final"]
    payload: dict[str, object]


class DeliveryBlocked(Exception):
    """Levée quand la note globale passe sous le seuil de livraison."""


def current_version() -> str:
    """Version courante : tag git si HEAD y est exactement (ex. run
    `release.yml` déclenché par `push: tags: v*.*.*`), sinon
    `dev-<commit court>` — ne lève jamais, une évaluation locale sans tag
    doit rester exécutable."""
    tag = current_git_tag()
    return tag if tag is not None else f"dev-{current_git_commit()}"


def _persist_version(
    session: Session, hashes: dict[str, str], commit: str, version_tag: str
) -> None:
    existing = session.get(AgentVersion, version_tag)
    if existing is not None:
        return  # même version déjà connue (hash identique) — pas de doublon
    session.add(
        AgentVersion(
            version_tag=version_tag,
            prompt_hash=hashes["prompt_hash"],
            memory_config_hash=hashes["memory_config_hash"],
            guardrail_config_hash=hashes["guardrail_config_hash"],
            gate_config_hash=hashes["gate_config_hash"],
            git_commit=commit,
        )
    )
    session.commit()


def _fetch_previous_quality_scores(session: Session) -> list[float]:
    """Scores qualité du dernier `EvalRun` déjà persisté (n'importe quelle
    version) — baseline de non-régression (M4). `[]` s'il n'y a encore aucun
    run (bootstrap : rien à comparer, le gate qualité ne peut pas encore
    bloquer pour du bruit — état transitoire assumé, pas un trou silencieux)."""
    previous_run = session.query(EvalRun).order_by(EvalRun.ran_at.desc()).first()
    if previous_run is None:
        return []
    cases = session.query(EvalCaseResult).filter_by(run_id=previous_run.id, suite="quality").all()
    return [c.score for c in cases]


def run_eval_steps(
    agent: Evaluable,
    *,
    db_url: str | None = None,
    triggered_by: str = "manual",
    sink: ObservabilitySink | None = None,
    agent_factory: Callable[[], Any] | None = None,
) -> Iterator[GateEvent]:
    """Exécute les trois suites (mémoire, garde-fous, qualité), calcule les
    notes, persiste `AgentVersion`/`EvalRun`/`EvalCaseResult` — même
    comportement que l'ancien `run_eval` (Task 6 d'origine), refactorisé en
    générateur pour que l'API `/mlops/gate/run` (chantier GUI) puisse
    diffuser la progression en direct. `run_eval()` ci-dessous consomme ce
    générateur jusqu'au bout et reste inchangé en signature/comportement pour
    tous ses appelants existants (CLI, tests d'acceptance).

    `db_url=None` est transmis tel quel aux suites et à `make_mlops_engine` —
    chacun résout alors `Settings.db_url` indépendamment (Postgres si
    joignable, sinon repli SQLite fichier). Ne jamais forcer un `:memory:` par
    défaut ici : un appel réel en CI/nightly doit respecter la base
    configurée, pas une base éphémère silencieuse.

    Si `sink` est déjà un `CostAccumulatingSink` (cas du CLI/API, qui en
    construisent un pour instrumenter l'agent évalué lui-même AVANT d'appeler
    ceci), il est réutilisé tel quel plutôt que ré-enveloppé : un double-wrap
    créerait un second accumulateur qui ne verrait jamais les appels LLM de
    l'agent (câblés sur le premier), sous-comptant le coût réel — c'est
    justement le bug que ce partage évite (voir `mlops.runner`/`mlops.cli`)."""
    sink = sink or NullSink()
    cost_sink = sink if isinstance(sink, CostAccumulatingSink) else CostAccumulatingSink(sink)

    yield GateEvent("suite_start", {"suite": "memory"})
    memory_results: list[CaseResult] = []
    for case_event in run_memory_suite_steps(db_url=db_url, sink=cost_sink):
        if case_event.kind == "start":
            yield GateEvent("case_start", {"suite": "memory", "case_id": case_event.case_id})
        else:
            assert case_event.result is not None
            memory_results.append(case_event.result)
            yield GateEvent(
                "case_done",
                {
                    "suite": "memory",
                    "case_id": case_event.case_id,
                    "passed": case_event.result.passed,
                    "score": case_event.result.score,
                },
            )
    note_memory = _pass_rate(memory_results)
    yield GateEvent(
        "suite_done",
        {
            "suite": "memory",
            "cases": len(memory_results),
            "passed": sum(1 for r in memory_results if r.passed),
            "note": note_memory,
        },
    )

    yield GateEvent("suite_start", {"suite": "guardrails"})
    guardrails_results: list[CaseResult] = []
    for case_event in run_guardrails_suite_steps(db_url=db_url, sink=cost_sink):
        if case_event.kind == "start":
            yield GateEvent("case_start", {"suite": "guardrails", "case_id": case_event.case_id})
        else:
            assert case_event.result is not None
            guardrails_results.append(case_event.result)
            yield GateEvent(
                "case_done",
                {
                    "suite": "guardrails",
                    "case_id": case_event.case_id,
                    "passed": case_event.result.passed,
                    "score": case_event.result.score,
                },
            )
    recall, fpr = guardrails_confusion_matrix(guardrails_results)
    note_guardrails = 0.6 * recall + 0.4 * (1 - fpr)
    yield GateEvent(
        "suite_done",
        {
            "suite": "guardrails",
            "cases": len(guardrails_results),
            "passed": sum(1 for r in guardrails_results if r.passed),
            "note": note_guardrails,
        },
    )

    yield GateEvent("suite_start", {"suite": "quality"})
    quality_results: list[CaseResult] = []
    for case_event in run_quality_suite_steps(agent, db_url=db_url):
        if case_event.kind == "start":
            yield GateEvent("case_start", {"suite": "quality", "case_id": case_event.case_id})
        else:
            assert case_event.result is not None
            quality_results.append(case_event.result)
            yield GateEvent(
                "case_done",
                {
                    "suite": "quality",
                    "case_id": case_event.case_id,
                    "passed": case_event.result.passed,
                    "score": case_event.result.score,
                },
            )
    quality_scores = [r.score for r in quality_results]
    note_quality = statistics.mean(quality_scores) if quality_scores else 0.0
    yield GateEvent(
        "suite_done",
        {
            "suite": "quality",
            "cases": len(quality_results),
            "passed": sum(1 for r in quality_results if r.passed),
            "note": note_quality,
        },
    )

    note_globale = 0.4 * note_memory + 0.4 * note_guardrails + 0.2 * note_quality

    # Suite Outils (Ch.4 §Évaluation). Exige un **agent frais par cas** : les cas
    # d'action mutent l'état métier (annulation, remboursement), et les enchaîner
    # sur une même base ferait dépendre un cas du précédent — un cas rejouable ne
    # doit dépendre que de lui-même.
    tools_results: list[CaseResult] = []
    note_tools: float | None = None
    tool_selection_accuracy: float | None = None
    if agent_factory is not None:
        yield GateEvent("suite_start", {"suite": "tools"})
        for case_event in run_tools_suite_steps(agent_factory):
            if case_event.kind == "start":
                yield GateEvent("case_start", {"suite": "tools", "case_id": case_event.case_id})
            else:
                assert case_event.result is not None
                tools_results.append(case_event.result)
                yield GateEvent(
                    "case_done",
                    {
                        "suite": "tools",
                        "case_id": case_event.case_id,
                        "passed": case_event.result.passed,
                        "score": case_event.result.score,
                    },
                )
        note_tools, tool_selection_accuracy = tools_scores(tools_results)
        yield GateEvent(
            "suite_done",
            {
                "suite": "tools",
                "cases": len(tools_results),
                "passed": sum(1 for r in tools_results if r.passed),
                "note": note_tools,
                "selection_accuracy": tool_selection_accuracy,
            },
        )

    all_results = memory_results + guardrails_results + quality_results + tools_results
    latencies = sorted(r.latency_ms for r in all_results)
    latency_p50 = _percentile(latencies, 0.5)
    latency_p95 = _percentile(latencies, 0.95)
    # Coût : `cost_sink` (`CostAccumulatingSink`) additionne chaque
    # `on_llm_call(..., cost=...)` émis par les composants instrumentés des
    # suites — `0.0` seulement si les composants sont restés en repli local
    # (`EchoLLM`/`LexicalClassifier`, coût nul par construction, cf.
    # `estimate_cost`), jamais un placeholder inconditionnel. Le SLO
    # (conception §Gates non-fonctionnels) est un coût **par conversation**
    # (0,05 €), pas un total sur l'ensemble des cas des 3 suites : diviser par
    # le nombre de conversations évaluées est nécessaire pour que le gate
    # compare des grandeurs homogènes (voir aussi `EvalRun.cost_per_conv`).
    cost = cost_sink.total_cost / len(all_results) if all_results else 0.0

    hashes = compute_version_hashes()
    commit = current_git_commit()
    version_tag = current_version()

    engine = make_mlops_engine(db_url)
    session_factory = sessionmaker(bind=engine, future=True)
    session = session_factory()
    try:
        baseline_quality_scores = _fetch_previous_quality_scores(session)
        # Qualité : dimension bruitée (jugement LLM) — le gate ne compare pas
        # `note_quality` brute au plancher, mais son delta à la baseline
        # (2σ, M4). Sans baseline (1er run), rien à comparer : la dimension
        # ne peut pas encore échouer pour "régression" (mais reste soumise au
        # `min()` comme les 2 autres dimensions).
        quality_gate_score = note_quality
        if baseline_quality_scores and quality_scores:
            if not non_regression_ok(baseline_quality_scores, quality_scores):
                quality_gate_score = 0.0

        gate_settings = get_settings()
        nf_gate_ok = (
            latency_p95 <= gate_settings.gate_latency_p95_ceiling_ms
            and cost <= gate_settings.gate_cost_per_conv_ceiling
        )
        # `note_tools` n'entre dans le `min` que si la suite a **tourné** : une
        # suite sautée (pas de `agent_factory`) doit laisser le gate inchangé, pas
        # le faire échouer à 0 — un run qui ne mesure pas n'est pas un run qui
        # régresse (même logique que les runs incomplets, §Robustesse du harness).
        gating_dims = [note_memory, note_guardrails, quality_gate_score]
        if note_tools is not None:
            gating_dims.append(note_tools)
        global_gate = min(gating_dims) if nf_gate_ok else 0.0

        _persist_version(session, hashes, commit, version_tag)
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        run = EvalRun(
            id=run_id,
            version_tag=version_tag,
            note_memory=note_memory,
            note_guardrails=note_guardrails,
            note_quality=note_quality,
            note_tools=note_tools,
            tool_selection_accuracy=tool_selection_accuracy,
            note_globale=note_globale,
            global_gate=global_gate,
            gate_passed=global_gate >= gate_settings.gate_min_score,
            block_rate=recall,
            false_positive_rate=fpr,
            latency_p50_ms=latency_p50,
            latency_p95_ms=latency_p95,
            cost_per_conv=cost,
            langfuse_trace_url=cost_sink.run_url(run_id),
            triggered_by=triggered_by,
        )
        session.add(run)
        session.commit()
        for result in all_results:
            session.add(
                EvalCaseResult(
                    id=f"case-{uuid.uuid4().hex[:8]}",
                    run_id=run_id,
                    case_id=result.case_id,
                    suite=result.suite,
                    passed=result.passed,
                    score=result.score,
                    latency_ms=result.latency_ms,
                    retried=result.retried,
                    error_kind=result.error_kind,
                )
            )
        session.commit()
    finally:
        session.close()

    # Mesures hors gate, dotées d'un propriétaire dans le rapport (audit
    # O-01/O-02/O-04). Chacune est *best-effort* : une mesure de reporting qui
    # ferait échouer un run de gate inverserait la hiérarchie — le gate décide,
    # le reporting informe.
    latency_by_component = component_latency_report(cost_sink.latencies_by_component)
    shadow_rate = _measure_shadow_divergence()
    extractor_precision, extractor_recall = _measure_extractor_quality()

    yield GateEvent(
        "final",
        {
            "note_memory": note_memory,
            "note_guardrails": note_guardrails,
            "note_quality": note_quality,
            "note_tools": note_tools,
            "tool_selection_accuracy": tool_selection_accuracy,
            "note_globale": note_globale,
            "global_gate": global_gate,
            "gate_passed": global_gate >= get_settings().gate_min_score,
            "block_rate": recall,
            "false_positive_rate": fpr,
            "latency_p50_ms": latency_p50,
            "latency_p95_ms": latency_p95,
            "cost_per_conv": cost,
            "run_id": run_id,
            "version_tag": version_tag,
            "latency_by_component": [row.to_dict() for row in latency_by_component],
            "judge_shadow_divergence_rate": shadow_rate,
            "extractor_precision": extractor_precision,
            "extractor_recall": extractor_recall,
        },
    )


def run_eval(
    agent: Evaluable,
    *,
    db_url: str | None = None,
    triggered_by: str = "manual",
    sink: ObservabilitySink | None = None,
    agent_factory: Callable[[], Any] | None = None,
) -> Scores:
    """Comportement et signature inchangés — consomme `run_eval_steps`
    jusqu'à son événement `final` et reconstruit `Scores` à l'identique.
    Voir `run_eval_steps` pour le détail étape par étape (utilisé par l'API
    `/mlops/gate/run` pour diffuser la progression en direct)."""
    final_payload: dict[str, object] | None = None
    for event in run_eval_steps(
        agent,
        db_url=db_url,
        triggered_by=triggered_by,
        sink=sink,
        agent_factory=agent_factory,
    ):
        if event.stage == "final":
            final_payload = event.payload
    assert final_payload is not None  # run_eval_steps yields exactly one "final" event

    return Scores(
        memory=final_payload["note_memory"],  # type: ignore[arg-type]
        guardrails=final_payload["note_guardrails"],  # type: ignore[arg-type]
        quality=final_payload["note_quality"],  # type: ignore[arg-type]
        global_=final_payload["global_gate"],  # type: ignore[arg-type]
        block_rate=final_payload["block_rate"],  # type: ignore[arg-type]
        false_positive_rate=final_payload["false_positive_rate"],  # type: ignore[arg-type]
        latency_ms=final_payload["latency_p95_ms"],  # type: ignore[arg-type]
        cost=final_payload["cost_per_conv"],  # type: ignore[arg-type]
        latency_by_component=[
            ComponentLatency.from_dict(row)
            for row in cast(list[dict[str, object]], final_payload["latency_by_component"])
        ],
        judge_shadow_divergence_rate=final_payload[  # type: ignore[arg-type]
            "judge_shadow_divergence_rate"
        ],
        extractor_precision=final_payload["extractor_precision"],  # type: ignore[arg-type]
        extractor_recall=final_payload["extractor_recall"],  # type: ignore[arg-type]
        tools=final_payload["note_tools"],  # type: ignore[arg-type]
        tool_selection_accuracy=final_payload[  # type: ignore[arg-type]
            "tool_selection_accuracy"
        ],
    )


def _measure_shadow_divergence(db_url: str | None = None) -> float | None:
    """Divergence du juge de repli en shadow mode (audit O-02). Best-effort :
    lire un agrégat de reporting ne doit jamais faire échouer un run de gate."""
    from velmo.guardrails.db import make_guardrails_engine
    from velmo.mlops.shadow import shadow_divergence_rate

    try:
        factory = sessionmaker(bind=make_guardrails_engine(db_url), future=True)
        session = factory()
        try:
            return shadow_divergence_rate(session)
        finally:
            session.close()
    except Exception:  # pragma: no cover - dépend de la disponibilité du store
        logger.warning("Divergence shadow non mesurée (journal inaccessible).", exc_info=True)
        return None


def _measure_extractor_quality() -> tuple[float | None, float | None]:
    """Précision/rappel d'écriture de l'extracteur (audit O-01). Best-effort
    et hors gate : le jeu labellisé porte des cas limites, donc du bruit."""
    from velmo.mlops.suites.extractor import run_extractor_suite

    try:
        result = run_extractor_suite()
    except Exception:  # pragma: no cover - fixture absente d'un checkout partiel
        logger.warning("Qualité de l'extracteur non mesurée.", exc_info=True)
        return None, None
    return result.precision, result.recall


def _pass_rate(results: list[CaseResult]) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if r.passed) / len(results)


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(int(len(sorted_values) * fraction), len(sorted_values) - 1)
    return sorted_values[index]


def enforce_threshold(scores: Scores, min_score: float) -> None:
    """Bloque la livraison (lève `DeliveryBlocked`) si `global_` (le
    `global_gate = min(dims)`) passe sous `min_score`."""
    if scores.global_ < min_score:
        raise DeliveryBlocked(
            f"Note globale {scores.global_:.2f} < seuil {min_score:.2f} — "
            f"mémoire={scores.memory:.2f} garde-fous={scores.guardrails:.2f} "
            f"qualité={scores.quality:.2f}"
        )


def write_report(scores: Scores, path: Path) -> None:
    """Écrit le rapport de suivi (note mémoire, blocage, faux positifs,
    latence, coût) — voir Task 7 pour le contrat complet."""
    from velmo.mlops.report import write_report as _write_report

    _write_report(scores, path)
