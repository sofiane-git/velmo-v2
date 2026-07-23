"""Construction de l'agent de gate MLOps : session seedée + composants
instrumentés — partagée par le CLI (`mlops/cli.py`) et la route API
`/mlops/gate/run` (`api.py`), pour ne pas dupliquer ce câblage entre les deux
points d'entrée.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from velmo.mlops import Evaluable
from velmo.mlops.observability import (
    InstrumentedClassifier,
    InstrumentedExtractor,
    InstrumentedJudge,
    InstrumentedLLM,
    ObservabilitySink,
)


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
    # SQLite seulement (repli hors-ligne/tests) — sur Postgres, le schéma vient
    # d'Alembic (D2-04), jamais d'un create_all concurrent.
    if engine.url.drivername.startswith("sqlite"):
        Base.metadata.create_all(engine, checkfirst=True)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    if session.execute(select(Order)).first() is None:
        seed(session)
        session.commit()
    return session


def build_gate_agent(sink: ObservabilitySink) -> Evaluable:
    """Assemble l'agent par défaut avec chaque composant LLM instrumenté.
    Chaque `get_*()` respecte son propre repli déjà établi (Azure si
    configuré, sinon `EchoLLM`/`LexicalClassifier`/`RuleBasedJudge`/
    `RuleBasedExtractor`) — cette fonction ne fait que les envelopper, jamais
    ne les remplace. Partagée entre le CLI (`mlops/cli.py`) et la route API
    `/mlops/gate/run` (`api.py`) — voir docstring de module."""
    from velmo.agent import build_default_agent
    from velmo.config import get_settings
    from velmo.guardrails import GuardrailEngine
    from velmo.guardrails.classifier import get_classifier
    from velmo.guardrails.judge import get_judge
    from velmo.llm import get_llm
    from velmo.memory import MemoryManager
    from velmo.memory.extractor import get_extractor

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
