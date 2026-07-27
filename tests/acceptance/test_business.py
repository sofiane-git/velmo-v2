"""Tests métier spécifiques au domaine (au-delà des 11 du brief).

Ces règles vivent dans les outils fonctionnels : elles passent sur `main` comme
sur `instructor` (l'agent est sûr par construction, avant même les garde-fous de
contenu).
"""

from __future__ import annotations

from conftest import refund_token

from velmo.db import Escalation, Order, OrderStatus, Refund, RefundStatus
from velmo.tools import get_order, trigger_refund, update_order_item
from velmo.tools._common import select


def test_cannot_modify_shipped_order(db_session):
    # Une commande expédiée n'est pas modifiable : escalade, pas de changement.
    result = update_order_item(db_session, "O-2024-0103", "C-marc-dubois", "M")
    assert result["action"] == "escalate"

    order = db_session.get(Order, "O-2024-0103")
    assert order.status == OrderStatus.shipped
    assert order.items[0].size.value == "L"  # taille inchangée


def test_can_modify_unshipped_order(db_session):
    result = update_order_item(db_session, "O-2024-0101", "C-marc-dubois", "XL")
    assert result["action"] == "updated"
    order = db_session.get(Order, "O-2024-0101")
    assert order.items[0].size.value == "XL"


def test_refund_above_cap_escalates(db_session):
    # Remboursement > 50€ : escalade, aucun remboursement auto.
    result = trigger_refund(
        db_session,
        "O-2024-0110",
        "C-sophie-martin",
        200,
        "Litige",
        intent_token=refund_token(db_session, "O-2024-0110", "C-sophie-martin", 200, "Litige"),
    )
    assert result["action"] == "escalate"
    autos = db_session.scalars(
        select(Refund).where(Refund.order_id == "O-2024-0110", Refund.status == RefundStatus.auto)
    ).all()
    assert autos == []


def test_refund_below_cap_is_auto(db_session):
    result = trigger_refund(
        db_session,
        "O-2024-0110",
        "C-sophie-martin",
        30,
        "Geste commercial",
        intent_token=refund_token(
            db_session, "O-2024-0110", "C-sophie-martin", 30, "Geste commercial"
        ),
    )
    assert result["action"] == "refunded"


def test_isolation_other_customer_order(db_session):
    # Marc ne peut pas accéder à la commande de Sophie.
    result = get_order(db_session, "O-2024-0107", "C-marc-dubois")
    assert result.get("error") == "not_found_or_forbidden"


def test_no_fabulation_when_out_of_stock(reference_agent):
    # Variante om-1993 / M est à 0 : l'agent dit indisponible, ne fabule pas.
    answer = reference_agent.respond(
        "C-marc-dubois", "Le maillot om-1993 en taille M est-il disponible ?"
    )
    assert "indisponible" in answer.lower()


def test_escalation_recorded_on_shipped_modification(db_session):
    before = len(db_session.scalars(select(Escalation)).all())
    update_order_item(db_session, "O-2024-0103", "C-marc-dubois", "M")
    after = len(db_session.scalars(select(Escalation)).all())
    assert after == before + 1


def test_refund_cap_comes_from_versioned_config(db_session, monkeypatch):
    """Le plafond décide quelles actions s'exécutent sans humain : il doit vivre
    en configuration versionnée (donc dans le hash de version), pas en constante
    de module invisible du gate (audit TOO-05)."""
    from velmo.mlops.versioning import compute_version_hashes

    before = compute_version_hashes()["guardrail_config_hash"]
    monkeypatch.setenv("REFUND_CAP_EUR", "500")
    assert compute_version_hashes()["guardrail_config_hash"] != before


def test_refund_cap_override_changes_the_escalation_boundary(db_session, monkeypatch):
    """Preuve que la valeur lue est bien celle de la config : un montant qui
    escaladait auparavant passe en automatique sous un plafond relevé."""
    monkeypatch.setenv("REFUND_CAP_EUR", "500")
    result = trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        200.0,
        "geste commercial",
        intent_token=refund_token(
            db_session, "O-2024-0101", "C-marc-dubois", 200.0, "geste commercial"
        ),
    )
    assert result["action"] == "refunded"
