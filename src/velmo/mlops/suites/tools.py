"""Suite Outils : rejoue `eval/tool_cases.jsonl` contre la couche d'actions
métier (Ch.4 §Évaluation, audit Z-01/TOO-09).

La couche qui **engage de l'argent** était la seule sans métrique : ni la
sélection d'outil, ni la validité des arguments, ni la correction des refus
n'étaient mesurées. Un agent qui choisit le mauvais outil, ou qui exécute une
action qu'il aurait dû refuser, ne faisait chuter aucune note.

**Deux natures de cas, deux régimes** — c'est la règle du Ch.3 appliquée ici :
ce qui est exactement vérifiable gate, ce qui relève du jugement commence en
reporting.

| `kind` | Ce qu'on vérifie | Régime |
| --- | --- | --- |
| `refusal` | une demande hors appartenance / hors fenêtre / au-dessus du plafond produit **le bon refus** | **gate** (déterministe) |
| `confirmation` | aucune action irréversible ne s'exécute sans jeton consommé (A4) | **gate** (déterministe) |
| `selection` | l'agent appelle l'outil attendu, avec le bon `order_id` | **reporting** |

`note_tools` (celle qui gate) ne contient donc **que** les cas déterministes. La
justesse de sélection est publiée à part (`tool_selection_accuracy`) : l'y inclure
ferait entrer du bruit de routage dans le gate, ce que M4 interdit.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from velmo.db import OrderItem, Size
from velmo.mlops.results import CaseResult, CaseStepEvent, with_retry

EVAL_PATH = Path(__file__).resolve().parents[4] / "eval" / "tool_cases.jsonl"

# Cas qui gatent : leur verdict est exact, pas un jugement.
GATING_KINDS = frozenset({"refusal", "confirmation"})


def _load_cases() -> list[dict[str, Any]]:
    text = EVAL_PATH.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _outcome_of(result: dict[str, Any]) -> str:
    """Issue observable d'un appel d'outil, telle que la fixture l'exprime.

    On lit le **résultat de l'outil** plutôt que le journal `tool_audit` : la
    fixture décrit ce que le client obtient, et un cas doit échouer si l'outil
    renvoie autre chose même quand le journal, lui, est correct.
    """
    if "error" in result:
        return "refused_ownership" if result["error"] == "not_found_or_forbidden" else "error"
    action = result.get("action")
    return str(action) if action is not None else "unknown"


def _add_second_item(session: Any, order_id: str) -> None:
    """Ajoute une 2ᵉ ligne à une commande, pour les cas d'ambiguïté de sélection.

    Nécessaire parce que **toutes** les commandes du jeu de données sont
    mono-article : sans cette préparation, le cas « quel article ? » ne pourrait
    pas se déclencher — c'est précisément ce qui avait laissé passer le défaut
    `items[0]`.
    """
    session.add(
        OrderItem(
            id=f"oi-eval-{order_id[-4:]}",
            order_id=order_id,
            variant_id="v-om-1993-L",
            size=Size.M,
            unit_price=180,
        )
    )
    session.commit()


def _run_refusal_case(case: dict[str, Any], session: Any) -> CaseResult:
    from velmo import tools as business_tools

    start = time.monotonic()
    try:
        if case.get("requires_multi_item"):
            _add_second_item(session, case["arguments"]["order_id"])

        tool = getattr(business_tools, case["tool"])
        arguments = dict(case["arguments"])
        order_id = arguments.pop("order_id")
        # Les outils de classe I exigent un jeton (A4) ; un cas `refusal` teste
        # une **autre** garde, donc on lui en fournit un valide pour que le refus
        # attendu soit bien celui qu'on mesure et non « confirmation manquante ».
        if case["tool"] in ("trigger_refund", "cancel_order"):
            from velmo.tools._intent import prepare_intent

            intent = prepare_intent(
                session,
                user_id=case["user_id"],
                tool=case["tool"],
                arguments={"order_id": order_id, **arguments},
                resource_id=order_id,
            )
            if intent.token is not None:
                arguments["intent_token"] = intent.token

        result = tool(session, order_id, case["user_id"], **arguments)
        passed = _outcome_of(result) == case["expected_outcome"]
        return CaseResult(
            case_id=case["id"],
            suite="tools",
            passed=passed,
            score=1.0 if passed else 0.0,
            latency_ms=(time.monotonic() - start) * 1000,
        )
    except Exception:
        return CaseResult(
            case_id=case["id"],
            suite="tools",
            passed=False,
            score=0.0,
            latency_ms=(time.monotonic() - start) * 1000,
            error_kind="infra",
        )


def _run_confirmation_case(case: dict[str, Any], session: Any) -> CaseResult:
    """Aucune action irréversible ne s'exécute sans jeton (A4).

    Deux conditions, toutes deux nécessaires : l'outil doit **répondre**
    `confirmation_required`, et l'état métier doit être **inchangé**. Vérifier
    seulement la réponse laisserait passer un outil qui agit puis dit le
    contraire.
    """
    from velmo import tools as business_tools
    from velmo.db import Order, Refund
    from velmo.tools._common import select

    start = time.monotonic()
    try:
        arguments = dict(case["arguments"])
        order_id = arguments.pop("order_id")
        tool = getattr(business_tools, case["tool"])
        result = tool(session, order_id, case["user_id"], **arguments)

        answered = result.get("action") == "confirmation_required"
        if case["tool"] == "trigger_refund":
            untouched = (
                session.scalars(select(Refund).where(Refund.order_id == order_id)).all() == []
            )
        else:
            order = session.get(Order, order_id)
            untouched = order is not None and order.status.value != "cancelled"

        passed = answered and untouched
        return CaseResult(
            case_id=case["id"],
            suite="tools",
            passed=passed,
            score=1.0 if passed else 0.0,
            latency_ms=(time.monotonic() - start) * 1000,
        )
    except Exception:
        return CaseResult(
            case_id=case["id"],
            suite="tools",
            passed=False,
            score=0.0,
            latency_ms=(time.monotonic() - start) * 1000,
            error_kind="infra",
        )


def _run_selection_case(case: dict[str, Any], agent: Any) -> CaseResult:
    """L'agent choisit-il le bon outil, avec le bon `order_id` ?

    On inspecte le **routage**, pas l'effet : une action de classe I s'arrête à la
    demande de confirmation (A4), et c'est suffisant — le choix d'outil est déjà
    observable à ce stade, sans rien exécuter.
    """
    start = time.monotonic()
    try:
        chosen: str | None = None
        chosen_order: str | None = None
        for event_type, payload in agent.respond_traced(case["user_id"], case["message"]):
            if event_type == "routing":
                detail = payload.get("detail", {})
                chosen = detail.get("tool_name")
                chosen_order = detail.get("order_id")

        tool_ok = chosen == case["expected_tool"]
        order_ok = chosen_order == case.get("expected_order_id")
        passed = tool_ok and order_ok
        return CaseResult(
            case_id=case["id"],
            suite="tools",
            passed=passed,
            score=1.0 if passed else 0.0,
            latency_ms=(time.monotonic() - start) * 1000,
        )
    except Exception:
        return CaseResult(
            case_id=case["id"],
            suite="tools",
            passed=False,
            score=0.0,
            latency_ms=(time.monotonic() - start) * 1000,
            error_kind="infra",
        )


def run_tools_suite_steps(agent_factory: Any) -> Iterator[CaseStepEvent]:
    """Rejoue la suite cas par cas.

    `agent_factory()` doit rendre un agent **frais** : les cas d'action mutent
    l'état métier (annulation, remboursement, retour), et les réutiliser sur une
    même base ferait dépendre un cas du précédent — exactement ce que des fixtures
    rejouables doivent éviter.
    """
    for case in _load_cases():
        yield CaseStepEvent(kind="start", case_id=case["id"])
        agent = agent_factory()
        try:
            if case["kind"] == "selection":
                result = with_retry(lambda c=case, a=agent: _run_selection_case(c, a))  # type: ignore[misc]
            elif case["kind"] == "confirmation":
                result = with_retry(
                    lambda c=case, a=agent: _run_confirmation_case(c, a.session)  # type: ignore[misc]
                )
            else:
                result = with_retry(
                    lambda c=case, a=agent: _run_refusal_case(c, a.session)  # type: ignore[misc]
                )
        finally:
            # Un agent frais par cas (docstring ci-dessus) laisse sinon 3
            # pools de connexions ouverts par cas jusqu'au GC — épuisement
            # Postgres constaté en prod sur un run réel (`Standard_B1ms`).
            agent.memory.close()
            agent.guardrails.close()
            agent.session.get_bind().dispose()
        yield CaseStepEvent(kind="done", case_id=case["id"], result=result)


def run_tools_suite(agent_factory: Any) -> list[CaseResult]:
    return [
        event.result
        for event in run_tools_suite_steps(agent_factory)
        if event.kind == "done" and event.result is not None
    ]


def tools_scores(results: list[CaseResult]) -> tuple[float, float]:
    """`(note_tools, tool_selection_accuracy)`.

    - **`note_tools`** — proportion des cas **déterministes** réussis (refus et
      confirmation). C'est elle qui gate.
    - **`tool_selection_accuracy`** — proportion des cas de sélection réussis,
      publiée en reporting. L'inclure dans la note qui gate ferait entrer du bruit
      de routage dans le blocage de livraison (M4).

    Les échecs d'infrastructure sont exclus des deux : un timeout n'est pas une
    régression de l'agent (Ch.3 §Robustesse du harness).
    """
    # Classé depuis le champ `kind` de la fixture, jamais depuis le nom du cas :
    # un renommage d'identifiant ne doit pas déplacer silencieusement un cas hors
    # du périmètre qui gate.
    kinds = {case["id"]: case["kind"] for case in _load_cases()}
    gating = [r for r in results if kinds.get(r.case_id) in GATING_KINDS]
    selection = [r for r in results if kinds.get(r.case_id) == "selection"]

    def _rate(subset: list[CaseResult]) -> float:
        counted = [r for r in subset if r.error_kind != "infra"]
        if not counted:
            return 0.0
        return sum(1 for r in counted if r.passed) / len(counted)

    return _rate(gating), _rate(selection)
