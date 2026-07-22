"""Tests d'acceptance — chantier Évaluation & MLOps."""

from __future__ import annotations

import pytest
from conftest import build_degraded_agent, build_reference_agent

from velmo.mlops import (
    DeliveryBlocked,
    current_version,
    enforce_threshold,
    run_eval,
    write_report,
)

_REAL_LLM_ENV_KEYS = (
    "AZURE_AI_INFERENCE_ENDPOINT",
    "AZURE_AI_INFERENCE_API_KEY",
    "AZURE_OPENAI_GUARD_ENDPOINT",
    "AZURE_OPENAI_GUARD_API_KEY",
    "ANTHROPIC_FOUNDRY_ENDPOINT",
    "ANTHROPIC_API_KEY",
    "OLLAMA_URL",
)


@pytest.fixture(autouse=True)
def _hermetic_offline_eval(monkeypatch, tmp_path):
    """Gate d'acceptance = mode dégradé DÉTERMINISTE (philosophie hybride,
    audit B4) : le vrai modèle est gaté à release/nightly, jamais ici.

    Sans cette isolation, le `.env` réel du poste (auto-chargé par deepeval à
    l'import pour toute la session pytest, cf. pyproject) branche de vrais
    appels Azure : latence p95 > plafond NF → `global_` forcé à 0.0 pour les
    DEUX agents, et `degraded < good` devient `0.0 < 0.0` (l'échec historique
    de `test_regression_blocks_delivery`). La DB partagée `var/velmo_mlops.db`
    polluerait de même la baseline de non-régression entre exécutions."""
    for key in _REAL_LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path}/acceptance_mlops.db")


def test_scores_produced_and_versioned():
    # Critère : note globale + notes mémoire / garde-fous / qualité, versionnées.
    scores = run_eval(build_reference_agent())
    assert scores.global_ is not None and 0.0 <= scores.global_ <= 1.0
    assert scores.memory is not None
    assert scores.guardrails is not None
    assert scores.quality is not None
    assert current_version()


def test_regression_blocks_delivery():
    # Critère : une régression fait chuter la note et bloque la livraison.
    good = run_eval(build_reference_agent())
    degraded = run_eval(build_degraded_agent())

    assert degraded.global_ < good.global_
    enforce_threshold(good, 0.8)  # ne doit pas lever
    with pytest.raises(DeliveryBlocked):
        enforce_threshold(degraded, 0.8)


def test_report_contains_signals(tmp_path):
    # Critère : note mémoire, taux de blocage, taux de faux positifs, latence, coût visibles.
    scores = run_eval(build_reference_agent())
    report = tmp_path / "report.md"
    write_report(scores, report)

    text = report.read_text(encoding="utf-8").lower()
    for signal in ["memoire", "blocage", "faux positif", "latence", "cout"]:
        assert signal in text
