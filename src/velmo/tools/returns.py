"""Outil de retour / échange."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..db import Return, ReturnStatus
from ._common import RETURNABLE_STATUSES, new_id, owned_order


def create_return(session: Session, order_id: str, user_id: str, reason: str) -> dict[str, Any]:
    """Ouvre une demande de retour/échange si la commande est dans la fenêtre de retour."""
    order = owned_order(session, order_id, user_id)
    if order is None:
        return {"error": "not_found_or_forbidden", "order_id": order_id}
    if order.status not in RETURNABLE_STATUSES:
        return {"action": "refused", "reason": "not_returnable", "status": order.status.value}
    return_id = new_id("rt")
    session.add(
        Return(id=return_id, order_id=order_id, reason=reason, status=ReturnStatus.requested)
    )
    session.commit()
    return {"action": "return_opened", "return_id": return_id, "order_id": order_id}
