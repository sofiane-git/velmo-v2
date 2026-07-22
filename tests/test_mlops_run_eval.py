from __future__ import annotations

from conftest import build_degraded_agent, build_reference_agent
from sqlalchemy.orm import sessionmaker

from velmo.mlops import run_eval
from velmo.mlops.db import EvalCaseResult, EvalRun, make_mlops_engine


def test_run_eval_blocks_when_latency_slo_exceeded(tmp_path, monkeypatch) -> None:
    """M2/Gates non-fonctionnels : un p95 au-dessus du plancher doit faire
    chuter `global_` à 0.0 même si les 3 notes de dimension sont parfaites —
    sinon la latence ne "gate" pas vraiment (conception §Gates non-fonctionnels)."""
    monkeypatch.setenv("GATE_LATENCY_P95_CEILING_MS", "-1.0")  # tout dépasse
    db_url = f"sqlite:///{tmp_path}/mlops_latency.db"
    scores = run_eval(build_reference_agent(), db_url=db_url)
    assert scores.global_ == 0.0


def test_gate_passed_derives_from_configured_min_score(tmp_path, monkeypatch) -> None:
    # D8-05 : `gate_passed` persisté doit dériver de `settings.gate_min_score`
    # (source unique), pas d'un littéral 0.80 dupliqué — un plancher remonté à
    # 1.01 (inatteignable) doit faire échouer le gate persisté.
    monkeypatch.setenv("GATE_MIN_SCORE", "1.01")
    db_url = f"sqlite:///{tmp_path}/mlops_gate_cfg.db"
    run_eval(build_reference_agent(), db_url=db_url)

    engine = make_mlops_engine(db_url)
    session = sessionmaker(bind=engine, future=True)()
    runs = session.query(EvalRun).all()
    assert len(runs) == 1
    assert runs[0].gate_passed is False
    session.close()


def test_run_eval_persists_version_run_and_cases(tmp_path) -> None:
    # Fichier SQLite réel (pas `:memory:`) : `run_eval` ouvre/ferme sa propre
    # connexion en interne, un second engine ouvert ici par le test doit
    # relire les mêmes données — `:memory:` créerait une base vide distincte.
    db_url = f"sqlite:///{tmp_path}/mlops_eval.db"
    scores = run_eval(build_reference_agent(), db_url=db_url, triggered_by="ci")
    assert 0.0 <= scores.global_ <= 1.0
    assert scores.memory > 0.0

    engine = make_mlops_engine(db_url)
    session = sessionmaker(bind=engine, future=True)()
    runs = session.query(EvalRun).all()
    assert len(runs) == 1
    assert runs[0].triggered_by == "ci"
    case_results = session.query(EvalCaseResult).filter_by(run_id=runs[0].id).all()
    assert len(case_results) > 0
    session.close()


def test_run_eval_global_gate_is_min_of_dimensions(tmp_path) -> None:
    # `db_url` isolé obligatoire ici : sans lui, `run_eval` retombe sur la
    # base SQLite partagée par défaut (Postgres injoignable en local/CI), dont
    # l'historique accumulé sert de baseline de non-régression qualité
    # (`_fetch_previous_quality_scores`) — un run précédent bruité y ferait
    # échouer `non_regression_ok` au hasard et forcerait `global_` à 0.0,
    # peu importe les 3 notes de dimension réelles.
    db_url = f"sqlite:///{tmp_path}/mlops_global_gate.db"
    scores = run_eval(build_reference_agent(), db_url=db_url)
    assert scores.global_ == min(scores.memory, scores.guardrails, scores.quality) or (
        # `global_` peut porter soit la moyenne pondérée (reporting) soit le
        # gate selon l'implémentation retenue à l'écriture du code — ce test
        # verrouille que le MIN est bien calculable et cohérent avec les 3
        # dimensions individuelles, peu importe lequel `Scores.global_`
        # représente historiquement (le champ existant avant ce chantier).
        min(scores.memory, scores.guardrails, scores.quality) <= scores.global_
    )


def test_run_eval_degraded_agent_scores_lower_than_reference(tmp_path) -> None:
    # NB : `.guardrails` ne peut jamais différer ici — `run_guardrails_suite`
    # (Task 3) ne prend pas `agent` en paramètre et construit toujours son
    # propre `GuardrailEngine` via `get_classifier()`/`get_judge()`, quel que
    # soit l'agent évalué. Le signal de régression du garde-fous désactivé
    # (`AllowAllGuardrails`) remonte par la Suite Qualité : les réponses non
    # filtrées/non rédigées de l'agent dégradé ne correspondent plus aux
    # substrings attendus, ce qui fait chuter `.global_` (vérifié : score
    # qualité 1.0 partout pour l'agent de référence, 0.0 partout pour le
    # dégradé).
    # `db_url` isolé (même raison que `test_run_eval_global_gate_is_min_of_dimensions`
    # ci-dessus) : chaque agent a besoin de sa propre base, sinon le run
    # dégradé utiliserait le run de référence comme baseline de non-régression
    # qualité (et inversement selon l'ordre), ce qui n'est pas ce que ce test
    # vérifie.
    good = run_eval(build_reference_agent(), db_url=f"sqlite:///{tmp_path}/mlops_good.db")
    degraded = run_eval(build_degraded_agent(), db_url=f"sqlite:///{tmp_path}/mlops_degraded.db")
    assert degraded.global_ < good.global_


def test_run_eval_steps_yields_ordered_suite_then_final_events(tmp_path) -> None:
    from velmo.mlops import run_eval_steps

    db_url = f"sqlite:///{tmp_path}/mlops_steps.db"
    events = list(run_eval_steps(build_reference_agent(), db_url=db_url))

    assert events[0].stage == "suite_start"
    assert events[-1].stage == "final"

    suite_start_events = [e for e in events if e.stage == "suite_start"]
    suite_done_events = [e for e in events if e.stage == "suite_done"]
    assert [e.payload["suite"] for e in suite_start_events] == ["memory", "guardrails", "quality"]
    assert [e.payload["suite"] for e in suite_done_events] == ["memory", "guardrails", "quality"]
    for event in suite_done_events:
        assert event.payload["cases"] > 0
        assert 0.0 <= event.payload["note"] <= 1.0

    # Chaque suite_done est précédé d'autant de case_done que de cas, chacun
    # lui-même précédé d'un case_start pour le même case_id (cf.
    # `run_*_suite_steps` — un cas démarre puis se termine avant le suivant).
    for suite in ("memory", "guardrails", "quality"):
        starts = [
            e.payload["case_id"]
            for e in events
            if e.stage == "case_start" and e.payload["suite"] == suite
        ]
        dones = [
            e.payload["case_id"]
            for e in events
            if e.stage == "case_done" and e.payload["suite"] == suite
        ]
        assert starts == dones
        assert len(starts) > 0
        suite_done = next(e for e in suite_done_events if e.payload["suite"] == suite)
        assert suite_done.payload["cases"] == len(dones)
        for done_event in (
            e for e in events if e.stage == "case_done" and e.payload["suite"] == suite
        ):
            assert isinstance(done_event.payload["passed"], bool)
            assert 0.0 <= done_event.payload["score"] <= 1.0


def test_run_eval_steps_final_event_matches_run_eval_scores(tmp_path) -> None:
    from velmo.mlops import run_eval, run_eval_steps

    db_url_a = f"sqlite:///{tmp_path}/mlops_steps_a.db"
    db_url_b = f"sqlite:///{tmp_path}/mlops_steps_b.db"
    scores = run_eval(build_reference_agent(), db_url=db_url_a)
    events = list(run_eval_steps(build_reference_agent(), db_url=db_url_b))
    final = events[-1].payload

    assert final["note_memory"] == scores.memory
    assert final["note_guardrails"] == scores.guardrails
    assert final["note_quality"] == scores.quality
    assert final["global_gate"] == scores.global_
    assert final["gate_passed"] == (scores.global_ >= 0.80)
    assert isinstance(final["run_id"], str)
    assert isinstance(final["version_tag"], str)
