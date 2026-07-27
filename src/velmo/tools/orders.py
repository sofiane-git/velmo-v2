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
from ._idempotency import idempotent_action
from ._intent import consume_intent


def _write_outcome(result: dict[str, Any]) -> str:
    """Issue d'une écriture de commande.

    Attention au cas contre-intuitif : `escalate` (commande déjà expédiée)
    **écrit** une escalade, ce n'est donc pas une issue « sans effet » — la
    traiter comme telle ferait réveiller deux fois un humain sur un simple retry.
    Seul le refus d'appartenance n'écrit rien.
    """
    if result.get("action") == "confirmation_required":
        return "error"  # rien écrit : la clé d'idempotence est libérée
    if "error" in result:
        return "refused_ownership"
    if result.get("action") in ("item_selection_required", "item_not_found"):
        # Rien n'a été écrit, et l'action redeviendra possible dès que le client
        # aura désigné l'article : la clé d'idempotence doit être libérée.
        return "error"
    if result.get("action") == "escalate":
        return "refused_state"
    return "ok"


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
    session: Session,
    order_id: str,
    user_id: str,
    new_size: str,
    *,
    item_id: str | None = None,
) -> dict[str, Any]:
    """Change la taille d'un article tant que la commande n'est pas expédiée.

    **Sélection de l'article.** `item_id` désigne explicitement la ligne à
    modifier. Omis, l'outil opère sur l'unique article de la commande — et
    **refuse** s'il y en a plusieurs, en listant les articles pour que le client
    tranche.

    C'est le correctif d'un défaut silencieux : l'outil modifiait `items[0]`, donc
    sur une commande multi-articles il changeait la taille d'un article que le
    client n'avait pas désigné, tout en répondant « c'est fait ». Refuser vaut
    mieux que deviner — une modification appliquée au mauvais article se découvre
    à la livraison, et le client n'a aucune raison de la soupçonner.

    Idempotent (Ch.4 §A5) : `item_id` fait partie de la clé, sinon modifier un
    second article passerait pour un rejeu du premier.
    """

    def _act() -> dict[str, Any]:
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
            session.flush()
            return {"action": "escalate", "reason": "already_shipped", "status": order.status.value}

        if item_id is None:
            if len(order.items) != 1:
                # Zéro article : rien à modifier (l'ancien code levait `IndexError`).
                # Plusieurs : ambiguïté que seul le client peut lever.
                if not order.items:
                    return {"action": "item_not_found", "order_id": order_id}
                return {
                    "action": "item_selection_required",
                    "order_id": order_id,
                    "items": [
                        {"item_id": it.id, "size": it.size.value, "variant_id": it.variant_id}
                        for it in order.items
                    ],
                }
            target = order.items[0]
        else:
            # Résolu **dans la commande**, jamais globalement : sinon `item_id`
            # deviendrait un moyen de modifier la ligne d'une autre commande —
            # voire d'un autre client — que celle désignée par `order_id`.
            matching = [it for it in order.items if it.id == item_id]
            if not matching:
                return {"action": "item_not_found", "order_id": order_id, "item_id": item_id}
            target = matching[0]

        target.size = Size(new_size)
        session.flush()
        return {
            "action": "updated",
            "order_id": order_id,
            "item_id": target.id,
            "new_size": new_size,
        }

    return idempotent_action(
        session,
        user_id=user_id,
        tool="update_order_item",
        tool_class="É",
        arguments={"order_id": order_id, "new_size": new_size, "item_id": item_id},
        resource_id=order_id,
        action=_act,
        outcome_of=_write_outcome,
    )


def update_shipping_address(
    session: Session, order_id: str, user_id: str, address: dict[str, Any]
) -> dict[str, Any]:
    """Modifie l'adresse de livraison tant que la commande n'est pas expédiée.

    Idempotent (Ch.4 §A5). L'adresse est une donnée personnelle : elle est
    **masquée** dans le journal d'actions (Ch.4 §A6).
    """

    def _act() -> dict[str, Any]:
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
            session.flush()
            return {"action": "escalate", "reason": "already_shipped", "status": order.status.value}
        order.shipping_address = address
        session.flush()
        return {"action": "updated", "order_id": order_id, "address": address}

    return idempotent_action(
        session,
        user_id=user_id,
        tool="update_shipping_address",
        tool_class="É",
        arguments={"order_id": order_id, "address": address},
        resource_id=order_id,
        action=_act,
        outcome_of=_write_outcome,
    )


def cancel_order(
    session: Session, order_id: str, user_id: str, *, intent_token: str | None = None
) -> dict[str, Any]:
    """Annule une commande tant qu'elle n'est pas expédiée.

    Écriture **irréversible** (classe I) : exige un jeton d'intention valide
    (Ch.4 §A4) et reste idempotente (§A5).
    """
    arguments = {"order_id": order_id}

    def _act() -> dict[str, Any]:
        # Consommation **dans** la garde d'idempotence : voir `refunds.py` pour le
        # raisonnement (un retry rejoue le même jeton, déjà consommé).
        verdict = consume_intent(
            session, token=intent_token, user_id=user_id, tool="cancel_order", arguments=arguments
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
        if order.status not in MODIFIABLE_STATUSES:
            session.add(
                Escalation(
                    id=new_id("esc"),
                    customer_id=user_id,
                    order_id=order_id,
                    reason="Annulation demandée sur commande expédiée",
                )
            )
            session.flush()
            return {"action": "escalate", "reason": "already_shipped", "status": order.status.value}
        order.status = OrderStatus.cancelled
        session.flush()
        return {"action": "cancelled", "order_id": order_id}

    return idempotent_action(
        session,
        user_id=user_id,
        tool="cancel_order",
        tool_class="I",
        arguments=arguments,
        resource_id=order_id,
        action=_act,
        outcome_of=_write_outcome,
    )
