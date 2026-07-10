"""Outil de remboursement, plafonné — au-delà, escalade obligatoire."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..db import Escalation, Refund, RefundStatus
from ._common import REFUND_CAP, new_id, owned_order


def trigger_refund(
    session: Session, order_id: str, user_id: str, amount: float, reason: str
) -> dict[str, Any]:
    """Rembourse une commande si le montant est sous le plafond, sinon escalade."""
    order = owned_order(session, order_id, user_id)
    if order is None:
        return {"error": "not_found_or_forbidden", "order_id": order_id}

    if amount > REFUND_CAP:
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
                reason=f"Remboursement {amount:.2f}€ au-dessus du plafond {REFUND_CAP:.0f}€",
            )
        )
        session.commit()
        return {"action": "escalate", "amount": amount, "cap": REFUND_CAP}

    refund_id = new_id("rf")
    session.add(
        Refund(
            id=refund_id, order_id=order_id, amount=amount, reason=reason, status=RefundStatus.auto
        )
    )
    session.commit()
    return {"action": "refunded", "refund_id": refund_id, "amount": amount}
