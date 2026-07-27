"""Mesures dotées d'un propriétaire dans le contrat de rapport — audit O-01/O-02/O-04.

Le défaut corrigé ici n'est pas une absence de mesure mais une absence de
**lecteur** : trois obligations déclarées dans les conceptions produisaient de la
donnée que rien n'agrégeait ni ne publiait.

- O-01 : `eval/memory_confidence_cases.jsonl` existait (27 cas labellisés) et sa
  seule trace dans le code était un commentaire de configuration — le composant
  qui décide ce qui entre en mémoire durable était le seul sans métrique.
- O-02 : la colonne `guardrail_audit.shadow_verdict` était écrite et jamais
  relue, donc le repli fail-closed de G5/G6 restait non calibré, ce que le
  shadow mode existait précisément pour éviter.
- O-04 : la latence était gatée sur un **total** que rien ne décomposait, donc un
  dépassement ne désignait aucun composant.
"""

from __future__ import annotations

import json
from pathlib import Path

from velmo.guardrails.db import make_guardrails_engine, write_audit
from velmo.mlops.budget import (
    ASYNC_COMPONENTS,
    LATENCY_ALLOCATION_MS,
    component_latency_report,
    synchronous_turn_p95_ms,
)
from velmo.mlops.observability import CostAccumulatingSink, NullSink
from velmo.mlops.shadow import shadow_divergence_rate
from velmo.mlops.suites.extractor import run_extractor_suite

# ------------------------------------------------------- O-04 latence par composant


def test_composed_turn_budget_fits_under_the_gated_ceiling():
    """La composition du tour doit tenir sous le plafond qui gate : une
    allocation qui dépasse son propre budget serait fictive.

    On compose comme le fait la conception (portes en parallèle, deux portes,
    extracteur asynchrone hors budget) — une somme naïve de la table
    surestimerait le tour de plus de 70 % et échouerait à tort."""
    from velmo.config import get_settings

    assert synchronous_turn_p95_ms() <= get_settings().gate_latency_p95_ceiling_ms


def test_async_component_is_excluded_from_the_turn_budget():
    """L'extracteur a l'allocation la plus large de la table sans peser sur le
    tour : c'est le sens de « asynchrone après la réponse »."""
    assert LATENCY_ALLOCATION_MS["memory_extractor"] not in (synchronous_turn_p95_ms(),)
    assert "memory_extractor" in ASYNC_COMPONENTS


def test_sink_collects_latency_per_component():
    sink = CostAccumulatingSink(NullSink())
    sink.on_llm_call("agent", tokens=10, latency_ms=1200.0, cost=0.01)
    sink.on_llm_call("agent", tokens=10, latency_ms=1400.0, cost=0.01)
    sink.on_llm_call("guardrails_judge", tokens=5, latency_ms=300.0, cost=0.001)
    assert sink.latencies_by_component["agent"] == [1200.0, 1400.0]
    assert sink.latencies_by_component["guardrails_judge"] == [300.0]


def test_component_report_flags_the_component_over_its_allocation():
    """Le point de la décomposition : nommer le coupable, pas constater un
    dépassement global."""
    rows = component_latency_report({"agent": [5000.0, 5200.0], "guardrails_judge": [100.0]})
    by_name = {r.component: r for r in rows}
    assert by_name["agent"].over_budget is True
    assert by_name["guardrails_judge"].over_budget is False


def test_component_report_ignores_unknown_components():
    """Un composant sans allocation n'est pas jugé (il serait faux de le
    déclarer hors budget), mais il reste publié."""
    rows = component_latency_report({"composant_inconnu": [10_000.0]})
    assert rows[0].allocation_ms is None
    assert rows[0].over_budget is False


# --------------------------------------------------- O-02 divergence du shadow


def _audit_session():
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=make_guardrails_engine("sqlite:///:memory:"), future=True)()


def test_shadow_divergence_rate_counts_only_comparable_rows():
    session = _audit_session()
    # Le juge cloud a bloqué, le shadow aussi -> accord.
    write_audit(
        session,
        "C-1",
        "out_of_scope",
        "input",
        "llm_judge",
        0.9,
        "block",
        None,
        shadow_verdict=json.dumps({"hors_role": 0.9}),
    )
    # Le juge cloud a bloqué, le shadow n'a rien vu -> divergence.
    write_audit(
        session,
        "C-1",
        "out_of_scope",
        "input",
        "llm_judge",
        0.9,
        "block",
        None,
        shadow_verdict=json.dumps({"hors_role": 0.0}),
    )
    # Hit sans shadow (regex) : hors périmètre du taux, pas un accord implicite.
    write_audit(session, "C-1", "pii", "output", "regex", None, "filter", None)
    session.commit()

    rate = shadow_divergence_rate(session)
    assert rate == 0.5


def test_shadow_divergence_rate_is_none_without_comparable_rows():
    """Aucune ligne comparable = pas de taux. Publier `0,0` laisserait croire à
    un repli parfaitement calibré alors qu'il n'a jamais été exercé."""
    session = _audit_session()
    write_audit(session, "C-1", "pii", "output", "regex", None, "filter", None)
    session.commit()
    assert shadow_divergence_rate(session) is None


# ------------------------------------------ O-01 précision de l'extracteur


def test_extractor_suite_runs_the_labelled_fixture():
    result = run_extractor_suite()
    assert result.total == 27
    assert 0.0 <= result.precision <= 1.0
    assert 0.0 <= result.recall <= 1.0


def test_extractor_suite_is_reporting_only():
    """Hors gate volontairement : la précision de l'extracteur est un jugement
    sur des cas limites, donc bruitée — elle informe la calibration du seuil,
    elle ne bloque pas une livraison."""
    assert run_extractor_suite().gates is False


def test_extractor_suite_detects_a_retained_case():
    result = run_extractor_suite()
    assert result.true_positives >= 1


# ------------------------------------------------- contrat de rapport complet


def test_report_publishes_every_owned_measure(tmp_path: Path):
    from velmo.mlops import Scores
    from velmo.mlops.report import write_report

    scores = Scores(
        memory=1.0,
        guardrails=0.95,
        quality=0.9,
        global_=0.9,
        block_rate=0.95,
        false_positive_rate=0.05,
        latency_ms=3000.0,
        cost=0.02,
        latency_by_component=component_latency_report({"agent": [1800.0]}),
        judge_shadow_divergence_rate=0.25,
        extractor_precision=0.8,
        extractor_recall=0.75,
    )
    path = tmp_path / "report.md"
    write_report(scores, path)

    sidecar = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert "latence_par_composant" in sidecar
    assert sidecar["taux_divergence_shadow"] == 0.25
    assert sidecar["extracteur_precision"] == 0.8
    assert sidecar["extracteur_rappel"] == 0.75

    markdown = path.read_text(encoding="utf-8")
    assert "agent" in markdown
    assert "Divergence" in markdown or "divergence" in markdown


def test_report_marks_unmeasured_fields_explicitly(tmp_path: Path):
    """Une mesure absente s'affiche comme non mesurée, jamais comme un zéro —
    un `0,0 %` de divergence se lirait comme un repli parfait."""
    from velmo.mlops import Scores
    from velmo.mlops.report import write_report

    scores = Scores(
        memory=1.0,
        guardrails=1.0,
        quality=1.0,
        global_=1.0,
        block_rate=1.0,
        false_positive_rate=0.0,
        latency_ms=100.0,
        cost=0.0,
    )
    path = tmp_path / "report.md"
    write_report(scores, path)
    sidecar = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert sidecar["taux_divergence_shadow"] is None
    assert "non mesuré" in path.read_text(encoding="utf-8")
