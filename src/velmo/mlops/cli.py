"""Point d'entrée CLI : `python -m velmo.mlops.score --min-score 0.8`.

Exécute les 3 suites contre un agent **instrumenté** via
`mlops.runner.build_gate_agent` (partagé avec la route API
`/mlops/gate/run`), applique le seuil de gate, écrit `mlops/report.md`
(+ sidecar JSON), sort en erreur si bloqué — c'est ce script que
`quality.yml` invoque (voir Step 6, activation du gate CI)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from velmo.mlops import DeliveryBlocked, enforce_threshold, run_eval, write_report
from velmo.mlops.runner import build_gate_agent


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate qualité MLOps Velmo 2.0")
    # Défaut résolu à l'exécution depuis Settings.gate_min_score (source
    # unique, audit D8-05) — `None` au parse pour rester surchargeable par
    # variable d'env en test comme en CI.
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--triggered-by", default="ci")
    args = parser.parse_args()

    from velmo.config import get_settings

    min_score = args.min_score if args.min_score is not None else get_settings().gate_min_score

    from velmo.mlops.observability import CostAccumulatingSink, get_sink

    # `LangfuseSink` réel si `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/
    # `LANGFUSE_BASE_URL` sont configurés, sinon `NullSink`. Le
    # `CostAccumulatingSink` est construit ICI et partagé entre l'agent
    # évalué et `run_eval` : sans ce partage, le coût des appels LLM de
    # l'agent lui-même échapperait au gate.
    raw_sink = get_sink()
    cost_sink = CostAccumulatingSink(raw_sink)
    agent = build_gate_agent(cost_sink)
    # `agent_factory` : la suite Outils exige un agent frais par cas (état métier
    # muté par les actions). Sans lui, elle serait sautée et la couche qui engage
    # de l'argent ne gaterait rien.
    scores = run_eval(
        agent,
        triggered_by=args.triggered_by,
        sink=cost_sink,
        agent_factory=lambda: build_gate_agent(cost_sink),
    )

    report_path = Path("mlops") / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_report(scores, report_path)

    # Process court-vécu (CLI) : force l'envoi des événements Langfuse
    # bufferisés avant sortie.
    close = getattr(raw_sink, "close", None)
    if close is not None:
        close()

    try:
        enforce_threshold(scores, min_score)
    except DeliveryBlocked as exc:
        print(f"GATE BLOQUÉ : {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Gate passé — note globale {scores.global_:.2%}")


if __name__ == "__main__":
    main()
