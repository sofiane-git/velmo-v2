"""Point d'entrée CLI : `python -m velmo.mlops.score --min-score 0.8`.

Exécute les 3 suites contre un agent **instrumenté** (chaque composant LLM
enveloppé par les wrappers `Instrumented*` de Task 5, injectés dans
`build_default_agent` — Task 8 Step 1), applique le seuil de gate, écrit
`mlops/report.md` (+ sidecar JSON), sort en erreur si bloqué — c'est ce
script que `quality.yml` invoque (voir Step 6, activation du gate CI)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from velmo.mlops import DeliveryBlocked, enforce_threshold, run_eval, write_report
from velmo.mlops.observability import ObservabilitySink


def _seeded_session() -> Session:
    """Session de l'agent évalué — schéma créé si absent (`checkfirst=True`,
    sans effet sur une base réelle déjà migrée), puis seedée avec le jeu de
    données de référence UNIQUEMENT si la table `orders` est vide (base
    fraîche, ex. CI sans service Postgres : repli SQLite vide). Une base
    réelle déjà peuplée (donnée de production) n'est jamais réécrite —
    condition sur `orders` vide, jamais un `seed()` inconditionnel, pour ne
    jamais dupliquer ou écraser de données réelles."""
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from velmo.db import Base, Order, make_engine
    from velmo.sampledata import seed

    engine = make_engine()
    Base.metadata.create_all(engine, checkfirst=True)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    if session.execute(select(Order)).first() is None:
        seed(session)
        session.commit()
    return session


def _build_instrumented_agent(sink: ObservabilitySink):  # type: ignore[no-untyped-def]
    """Assemble l'agent par défaut avec chaque composant LLM instrumenté —
    voir Task 5 (wrappers) et Task 8 Step 1 (`build_default_agent` DI).
    Chaque `get_*()` respecte son propre repli déjà établi (Azure si
    configuré, sinon `EchoLLM`/`LexicalClassifier`/`RuleBasedJudge`/
    `RuleBasedExtractor`) — cette fonction ne fait que les envelopper, jamais
    ne les remplace. `session` : voir `_seeded_session` — la suite Qualité
    (Task 4) pose des questions de commande/stock qui exigent des données de
    référence, absentes d'une base fraîche par défaut."""
    from velmo.agent import build_default_agent
    from velmo.config import get_settings
    from velmo.guardrails import GuardrailEngine
    from velmo.guardrails.classifier import get_classifier
    from velmo.guardrails.judge import get_judge
    from velmo.llm import get_llm
    from velmo.memory import MemoryManager
    from velmo.memory.extractor import get_extractor
    from velmo.mlops.observability import (
        InstrumentedClassifier,
        InstrumentedExtractor,
        InstrumentedJudge,
        InstrumentedLLM,
    )

    settings = get_settings()
    raw_llm = get_llm()
    llm = InstrumentedLLM(raw_llm, sink, "agent", settings.azure_ai_inference_model)
    memory = MemoryManager(
        extractor=InstrumentedExtractor(
            get_extractor(), sink, "memory_extractor", settings.anthropic_async_model
        ),
        llm=InstrumentedLLM(raw_llm, sink, "memory_summary", settings.azure_ai_inference_model),
    )
    guardrails = GuardrailEngine(
        classifier=InstrumentedClassifier(get_classifier(), sink, "guardrails_classifier"),
        judge=InstrumentedJudge(
            get_judge(), sink, "guardrails_judge", settings.azure_openai_guard_deployment
        ),
    )
    return build_default_agent(
        session=_seeded_session(), llm=llm, memory=memory, guardrails=guardrails
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate qualité MLOps Velmo 2.0")
    parser.add_argument("--min-score", type=float, default=0.80)
    parser.add_argument("--triggered-by", default="ci")
    args = parser.parse_args()

    from velmo.mlops.observability import CostAccumulatingSink, get_sink

    # `LangfuseSink` réel si `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/
    # `LANGFUSE_BASE_URL` sont configurés (Task 5), sinon `NullSink` — même
    # repli gracieux que `get_llm`/`get_classifier`/`get_judge`. Le
    # `CostAccumulatingSink` est construit ICI (pas dans `run_eval`) et
    # partagé entre l'agent évalué et `run_eval` : sans ce partage, le coût
    # des appels LLM de l'agent lui-même (le plus gros poste de coût réel)
    # échapperait totalement au gate — `run_eval` le détecte déjà construit
    # (`isinstance`) et le réutilise au lieu de le ré-envelopper.
    raw_sink = get_sink()
    cost_sink = CostAccumulatingSink(raw_sink)
    agent = _build_instrumented_agent(cost_sink)
    scores = run_eval(agent, triggered_by=args.triggered_by, sink=cost_sink)

    report_path = Path("mlops") / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_report(scores, report_path)

    # Process court-vécu (CLI) : force l'envoi des événements Langfuse
    # bufferisés avant sortie (doc SDK §Client lifecycle & flushing).
    # `close()` est appelé sur le sink réel (Langfuse), pas sur
    # `CostAccumulatingSink` qui ne le proxifie pas — `NullSink` n'a pas de
    # `close()` non plus (pas dans le Protocol, optionnel).
    close = getattr(raw_sink, "close", None)
    if close is not None:
        close()

    try:
        enforce_threshold(scores, args.min_score)
    except DeliveryBlocked as exc:
        print(f"GATE BLOQUÉ : {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Gate passé — note globale {scores.global_:.2%}")


if __name__ == "__main__":
    main()
