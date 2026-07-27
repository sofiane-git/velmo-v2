"""Outil de retour / échange."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..db import Return, ReturnStatus
from ._common import RETURNABLE_STATUSES, new_id, owned_order
from ._idempotency import idempotent_action


def _outcome(result: dict[str, Any]) -> str:
    if "error" in result:
        return "refused_ownership"
    if result.get("action") == "refused":
        # Refus d'état **sans écriture** ici (contrairement à `cancel_order`, qui
        # ouvre une escalade) : la clé est donc libérée et la demande reste
        # possible une fois la commande livrée.
        return "refused_ownership"
    return "ok"


def create_return(session: Session, order_id: str, user_id: str, reason: str) -> dict[str, Any]:
    """Ouvre une demande de retour/échange si la commande est dans la fenêtre de
    retour. Idempotent (Ch.4 §A5) : un retry n'ouvre pas un second dossier."""

    def _act() -> dict[str, Any]:
        order = owned_order(session, order_id, user_id)
        if order is None:
            return {"error": "not_found_or_forbidden", "order_id": order_id}
        if order.status not in RETURNABLE_STATUSES:
            return {"action": "refused", "reason": "not_returnable", "status": order.status.value}
        return_id = new_id("rt")
        session.add(
            Return(id=return_id, order_id=order_id, reason=reason, status=ReturnStatus.requested)
        )
        session.flush()
        return {"action": "return_opened", "return_id": return_id, "order_id": order_id}

    return idempotent_action(
        session,
        user_id=user_id,
        tool="create_return",
        tool_class="É",
        arguments={"order_id": order_id, "reason": reason},
        resource_id=order_id,
        action=_act,
        outcome_of=_outcome,
    )
