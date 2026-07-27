"""Outil de remboursement, plafonné — au-delà, escalade obligatoire."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..db import Escalation, Refund, RefundStatus
from ._common import new_id, owned_order, refund_cap
from ._idempotency import idempotent_action
from ._intent import consume_intent


def _outcome(result: dict[str, Any]) -> str:
    if result.get("action") == "confirmation_required":
        # Rien n'a été écrit : la clé d'idempotence doit être libérée pour que
        # l'action reste possible une fois la confirmation obtenue.
        return "error"
    if "error" in result:
        return "refused_ownership"
    if result.get("action") == "escalate":
        return "capped"
    return "ok"


def trigger_refund(
    session: Session,
    order_id: str,
    user_id: str,
    amount: float,
    reason: str,
    *,
    intent_token: str | None = None,
) -> dict[str, Any]:
    """Rembourse une commande si le montant est sous le plafond, sinon escalade.

    **Classe I** — deux contrôles non négociables, dans cet ordre :

    - **A4 confirmation** : sans jeton d'intention valide, l'outil n'exécute rien.
      Le jeton atteste qu'une confirmation a été demandée à un tour antérieur et
      qu'elle portait sur **ces** arguments (voir `_intent`).
    - **A5 idempotence** : deux appels de contenu identique produisent un seul
      mouvement d'argent — un retry réseau y coûtait un second remboursement réel.
    """
    arguments = {"order_id": order_id, "amount": amount, "reason": reason}

    def _act() -> dict[str, Any]:
        # Le jeton est consommé **ici**, à l'intérieur de la garde d'idempotence,
        # et non avant elle. Ordre décisif : un retry réseau rejoue le même appel
        # *avec le même jeton*, or celui-ci est à usage unique. Consommer d'abord
        # renverrait `already_consumed` au lieu du résultat d'origine — le client
        # re-confirmerait, et paierait deux fois. Avec cet ordre, un rejeu est
        # servi depuis le résultat mémorisé sans toucher au jeton.
        verdict = consume_intent(
            session, token=intent_token, user_id=user_id, tool="trigger_refund", arguments=arguments
        )
        if not verdict.ok:
            return {
                "action": "confirmation_required",
                "reason": verdict.reason,
                "order_id": order_id,
            }
        order = owned_order(session, order_id, user_id)
        if order is None:
            return {"error": "not_found_or_forbidden", "order_id": order_id}

        cap = refund_cap()
        if amount > cap:
            session.add(
                Refund(
                    id=new_id("rf"),
                    order_id=order_id,
                    amount=amount,
                    reason=reason,
                    status=RefundStatus.escalated,
                )
            )
            session.add(
                Escalation(
                    id=new_id("esc"),
                    customer_id=user_id,
                    order_id=order_id,
                    reason=f"Remboursement {amount:.2f}€ au-dessus du plafond {cap:.0f}€",
                )
            )
            session.flush()
            return {"action": "escalate", "amount": amount, "cap": cap}

        refund_id = new_id("rf")
        session.add(
            Refund(
                id=refund_id,
                order_id=order_id,
                amount=amount,
                reason=reason,
                status=RefundStatus.auto,
            )
        )
        session.flush()
        return {"action": "refunded", "refund_id": refund_id, "amount": amount}

    return idempotent_action(
        session,
        user_id=user_id,
        tool="trigger_refund",
        tool_class="I",
        arguments=arguments,
        resource_id=order_id,
        action=_act,
        outcome_of=_outcome,
    )
