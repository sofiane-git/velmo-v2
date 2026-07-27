"""Outil d'escalade vers un agent humain — deux canaux distincts (support vs
sécurité), cf. conception_chantier2_guardrails.md §Que fait l'agent."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from ..db import Escalation
from ._common import new_id
from ._idempotency import idempotent_action


def escalate_to_human(
    session: Session,
    customer_id: str,
    reason: str,
    order_id: str | None = None,
    *,
    channel: Literal["support", "security"] = "support",
) -> dict[str, Any]:
    """Passe la main à un humain. `channel="support"` (défaut) pour un risque
    humain (menace concrète G2, litige métier — SLA : accusé de prise en
    charge sous 1h ouvrée) ; `channel="security"` pour un risque technique
    (fuite confirmée G7, récidive d'injection G6) — deux files distinctes,
    cf. doc de conception. Notification par le canal gratuit déjà en place
    (email/webhook) ; pas d'outil de ticketing dédié à ce stade."""

    def _act() -> dict[str, Any]:
        escalation_id = new_id("esc")
        session.add(
            Escalation(
                id=escalation_id,
                customer_id=customer_id,
                order_id=order_id,
                reason=reason,
                channel=channel,
            )
        )
        session.flush()
        return {
            "action": "escalated",
            "escalation_id": escalation_id,
            "reason": reason,
            "channel": channel,
        }

    # Idempotent (Ch.4 §A5) : une escalade dupliquée réveille deux fois un humain
    # — même classe de problème qu'un double remboursement, coût différent.
    return idempotent_action(
        session,
        user_id=customer_id,
        tool="escalate_to_human",
        tool_class="É",
        arguments={"reason": reason, "order_id": order_id, "channel": channel},
        resource_id=order_id,
        action=_act,
    )
