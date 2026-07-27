"""Outil de remboursement, plafonné — au-delà, escalade obligatoire."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..db import Escalation, Refund, RefundStatus
from ._common import new_id, owned_order, refund_cap
from ._idempotency import idempotent_action


def _outcome(result: dict[str, Any]) -> str:
    if "error" in result:
        return "refused_ownership"
    if result.get("action") == "escalate":
        return "capped"
    return "ok"


def trigger_refund(
    session: Session, order_id: str, user_id: str, amount: float, reason: str
) -> dict[str, Any]:
    """Rembourse une commande si le montant est sous le plafond, sinon escalade.

    Idempotent (Ch.4 §A5) : deux appels de contenu identique produisent **un
    seul** mouvement d'argent. C'est l'outil qui motivait le contrat — un retry
    réseau y coûtait un second remboursement réel.
    """

    def _act() -> dict[str, Any]:
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
        arguments={"order_id": order_id, "amount": amount, "reason": reason},
        resource_id=order_id,
        action=_act,
        outcome_of=_outcome,
    )
