"""Outils de gestion des commandes (lecture et actions encadrées)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..db import Escalation, OrderStatus, Shipment, Size
from ._common import (
    MODIFIABLE_STATUSES,
    new_id,
    order_to_dict,
    owned_order,
    select,
)


def get_order(session: Session, order_id: str, user_id: str) -> dict[str, Any]:
    """Renvoie le détail et le statut d'une commande appartenant au client."""
    order = owned_order(session, order_id, user_id)
    if order is None:
        return {"error": "not_found_or_forbidden", "order_id": order_id}
    return order_to_dict(order)


def track_shipment(session: Session, order_id: str, user_id: str) -> dict[str, Any]:
    """Renvoie le suivi transporteur et la date estimée de livraison d'une commande."""
    order = owned_order(session, order_id, user_id)
    if order is None:
        return {"error": "not_found_or_forbidden", "order_id": order_id}
    shipment = session.scalars(select(Shipment).where(Shipment.order_id == order_id)).first()
    if shipment is None:
        return {"order_id": order_id, "status": order.status.value, "shipment": None}
    return {
        "order_id": order_id,
        "carrier": shipment.carrier,
        "tracking_number": shipment.tracking_number,
        "estimated_delivery": shipment.estimated_delivery,
        "actual_delivery": shipment.actual_delivery,
    }


def update_order_item(
    session: Session, order_id: str, user_id: str, new_size: str
) -> dict[str, Any]:
    """Change la taille d'un article tant que la commande n'est pas expédiée."""
    order = owned_order(session, order_id, user_id)
    if order is None:
        return {"error": "not_found_or_forbidden", "order_id": order_id}
    if order.status not in MODIFIABLE_STATUSES:
        session.add(
            Escalation(
                id=new_id("esc"),
                customer_id=user_id,
                order_id=order_id,
                reason=f"Modification demandée sur commande {order.status.value}",
            )
        )
        session.commit()
        return {"action": "escalate", "reason": "already_shipped", "status": order.status.value}
    order.items[0].size = Size(new_size)
    session.commit()
    return {"action": "updated", "order_id": order_id, "new_size": new_size}


def update_shipping_address(
    session: Session, order_id: str, user_id: str, address: dict[str, Any]
) -> dict[str, Any]:
    """Modifie l'adresse de livraison tant que la commande n'est pas expédiée."""
    order = owned_order(session, order_id, user_id)
    if order is None:
        return {"error": "not_found_or_forbidden", "order_id": order_id}
    if order.status not in MODIFIABLE_STATUSES:
        session.add(
            Escalation(
                id=new_id("esc"),
                customer_id=user_id,
                order_id=order_id,
                reason="Changement d'adresse sur commande expédiée",
            )
        )
        session.commit()
        return {"action": "escalate", "reason": "already_shipped", "status": order.status.value}
    order.shipping_address = address
    session.commit()
    return {"action": "updated", "order_id": order_id, "address": address}


def cancel_order(session: Session, order_id: str, user_id: str) -> dict[str, Any]:
    """Annule une commande tant qu'elle n'est pas expédiée."""
    order = owned_order(session, order_id, user_id)
    if order is None:
        return {"error": "not_found_or_forbidden", "order_id": order_id}
    if order.status not in MODIFIABLE_STATUSES:
        session.add(
            Escalation(
                id=new_id("esc"),
                customer_id=user_id,
                order_id=order_id,
                reason="Annulation demandée sur commande expédiée",
            )
        )
        session.commit()
        return {"action": "escalate", "reason": "already_shipped", "status": order.status.value}
    order.status = OrderStatus.cancelled
    session.commit()
    return {"action": "cancelled", "order_id": order_id}
