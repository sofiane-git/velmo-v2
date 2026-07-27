"""Idempotence et journal des actions métier — Ch.4 §A5/A6 (audit Z-01).

Le trou que ces tests ferment est le plus coûteux du dispositif : chaque outil
d'écriture générait un identifiant neuf et committait, sans aucune contrainte en
base. Un retry — retry réseau, tour rejoué, agent qui rappelle l'outil parce que
la réponse a été perdue — produisait **deux remboursements**.

Rappel de la décision de conception vérifiée ici : la clé d'idempotence est
**dérivée du contenu** (utilisateur, outil, arguments normalisés), pas d'un
identifiant de requête — un identifiant par appel serait neuf à chaque retry,
donc précisément inutile dans le seul cas qu'il doit couvrir.
"""

from __future__ import annotations

import pytest

from conftest import cancel_token, refund_token

from velmo.db import Escalation, Refund, Return, ToolAudit
from velmo.tools import (
    cancel_order,
    create_return,
    escalate_to_human,
    get_order,
    trigger_refund,
    update_shipping_address,
)
from velmo.tools._common import select

# ------------------------------------------------------------------ A5 idempotence


def test_replayed_refund_produces_a_single_effect(db_session):
    """Le cas qui motive le lot : deux appels identiques = un seul mouvement
    d'argent."""
    first = trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        30.0,
        "geste commercial",
        intent_token=refund_token(
            db_session, "O-2024-0101", "C-marc-dubois", 30.0, "geste commercial"
        ),
    )
    second = trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        30.0,
        "geste commercial",
        intent_token=refund_token(
            db_session, "O-2024-0101", "C-marc-dubois", 30.0, "geste commercial"
        ),
    )

    refunds = db_session.scalars(select(Refund).where(Refund.order_id == "O-2024-0101")).all()
    assert len(refunds) == 1
    assert first["refund_id"] == second["refund_id"]


def test_replay_returns_the_original_result_not_an_error(db_session):
    """Un rejeu doit être transparent pour l'appelant : il renvoie le résultat du
    premier appel. Lever une erreur obligerait l'agent à distinguer « déjà fait »
    de « échec », et il choisirait mal."""
    first = trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        12.5,
        "retard",
        intent_token=refund_token(db_session, "O-2024-0101", "C-marc-dubois", 12.5, "retard"),
    )
    second = trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        12.5,
        "retard",
        intent_token=refund_token(db_session, "O-2024-0101", "C-marc-dubois", 12.5, "retard"),
    )
    assert first == second
    assert second["action"] == "refunded"


def test_different_amount_is_a_new_action(db_session):
    """La clé dérive des arguments : un montant différent est une action
    différente, pas un rejeu."""
    trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        10.0,
        "retard",
        intent_token=refund_token(db_session, "O-2024-0101", "C-marc-dubois", 10.0, "retard"),
    )
    trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        20.0,
        "retard",
        intent_token=refund_token(db_session, "O-2024-0101", "C-marc-dubois", 20.0, "retard"),
    )
    refunds = db_session.scalars(select(Refund).where(Refund.order_id == "O-2024-0101")).all()
    assert len(refunds) == 2


def test_different_reason_is_a_new_action(db_session):
    """Contrepartie assumée de la clé par contenu : un second remboursement
    réellement voulu du même montant doit porter un motif distinct. C'est la
    friction acceptée pour ne jamais payer deux fois."""
    trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        10.0,
        "retard",
        intent_token=refund_token(db_session, "O-2024-0101", "C-marc-dubois", 10.0, "retard"),
    )
    trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        10.0,
        "article abime",
        intent_token=refund_token(
            db_session, "O-2024-0101", "C-marc-dubois", 10.0, "article abime"
        ),
    )
    refunds = db_session.scalars(select(Refund).where(Refund.order_id == "O-2024-0101")).all()
    assert len(refunds) == 2


def test_same_action_by_another_user_is_not_a_replay(db_session):
    """La clé inclut l'utilisateur : deux clients ne se volent pas leurs rejeux.

    Éprouvé sur un outil de classe **É** : depuis A4, un outil de classe I refuse
    un non-propriétaire encore plus tôt (jeton absent), si bien que le refus
    d'appartenance n'y est plus atteignable — la défense la plus précoce gagne.
    """
    first = create_return(db_session, "O-2024-0110", "C-sophie-martin", "taille")
    other = create_return(db_session, "O-2024-0110", "C-marc-dubois", "taille")
    assert first["action"] == "return_opened"
    assert other.get("error") == "not_found_or_forbidden"


def test_replayed_return_produces_a_single_effect(db_session):
    # Compté en delta : le jeu de données seedé contient déjà des retours sur
    # cette commande — un comptage absolu testerait le seed, pas l'idempotence.
    def _count() -> int:
        return len(db_session.scalars(select(Return).where(Return.order_id == "O-2024-0110")).all())

    before = _count()
    create_return(db_session, "O-2024-0110", "C-sophie-martin", "taille trop petite")
    after_first = _count()
    create_return(db_session, "O-2024-0110", "C-sophie-martin", "taille trop petite")
    assert after_first == before + 1
    assert _count() == after_first


def test_replayed_escalation_produces_a_single_ticket(db_session):
    """Une escalade dupliquée réveille deux fois un humain — même classe de
    problème qu'un double remboursement, coût différent."""
    first = escalate_to_human(db_session, "C-marc-dubois", "litige authenticite")
    second = escalate_to_human(db_session, "C-marc-dubois", "litige authenticite")
    assert first["escalation_id"] == second["escalation_id"]


def test_ownership_refusal_leaves_the_action_retryable(db_session):
    """Un refus d'appartenance n'écrit rien : la clé doit être libérée, sinon une
    action qui redeviendra légitime resterait bloquée par un refus passé."""
    create_return(db_session, "O-2024-0110", "C-marc-dubois", "pas la sienne")
    row = db_session.scalars(
        select(ToolAudit).where(ToolAudit.outcome == "refused_ownership")
    ).first()
    assert row is not None
    assert row.idempotency_key is None


def test_state_refusal_is_deduplicated_because_it_escalates(db_session):
    """Contre-intuitif mais décisif : un refus d'état **ouvre une escalade**,
    donc il écrit. Le traiter comme « sans effet » ferait réveiller deux fois un
    humain sur un simple retry."""

    def _count() -> int:
        return len(
            db_session.scalars(select(Escalation).where(Escalation.order_id == "O-2024-0103")).all()
        )

    before = _count()
    first = cancel_order(
        db_session,
        "O-2024-0103",
        "C-marc-dubois",
        intent_token=cancel_token(db_session, "O-2024-0103", "C-marc-dubois"),
    )
    assert first["action"] == "escalate"  # commande expédiée
    after_first = _count()
    cancel_order(
        db_session,
        "O-2024-0103",
        "C-marc-dubois",
        intent_token=cancel_token(db_session, "O-2024-0103", "C-marc-dubois"),
    )
    assert after_first == before + 1
    assert _count() == after_first  # le retry ne réveille pas un 2e humain


def test_capped_refund_is_deduplicated_because_it_escalates(db_session):
    """Même raison : au-dessus du plafond, l'outil écrit un remboursement
    `escalated` **et** une escalade."""
    trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        500.0,
        "gros litige",
        intent_token=refund_token(db_session, "O-2024-0101", "C-marc-dubois", 500.0, "gros litige"),
    )
    trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        500.0,
        "gros litige",
        intent_token=refund_token(db_session, "O-2024-0101", "C-marc-dubois", 500.0, "gros litige"),
    )
    refunds = db_session.scalars(select(Refund).where(Refund.order_id == "O-2024-0101")).all()
    assert len(refunds) == 1


def test_read_tools_are_not_journaled_as_actions(db_session):
    """Les lectures sont idempotentes par nature : les journaliser gonflerait le
    journal d'actions sans rien apprendre (même arbitrage que le `recall`
    optionnel du journal mémoire)."""
    get_order(db_session, "O-2024-0101", "C-marc-dubois")
    rows = db_session.scalars(select(ToolAudit).where(ToolAudit.tool == "get_order")).all()
    assert rows == []


# ------------------------------------------------------------- A6 journal d'actions


def test_successful_action_is_journaled(db_session):
    trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        15.0,
        "retard",
        intent_token=refund_token(db_session, "O-2024-0101", "C-marc-dubois", 15.0, "retard"),
    )
    rows = db_session.scalars(select(ToolAudit).where(ToolAudit.tool == "trigger_refund")).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == "C-marc-dubois"
    assert row.tool_class == "I"
    assert row.outcome == "ok"
    assert row.resource_id == "O-2024-0101"


def test_refusal_is_journaled_with_its_outcome(db_session):
    """Les refus sont le signal d'abus le plus direct : un journal qui ne garde
    que les succès ne les verrait jamais."""
    create_return(db_session, "O-2024-0110", "C-marc-dubois", "pas la sienne")
    rows = db_session.scalars(select(ToolAudit).where(ToolAudit.tool == "create_return")).all()
    assert len(rows) == 1
    assert rows[0].outcome == "refused_ownership"


def test_capped_refund_is_journaled_as_capped(db_session):
    trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        500.0,
        "gros litige",
        intent_token=refund_token(db_session, "O-2024-0101", "C-marc-dubois", 500.0, "gros litige"),
    )
    rows = db_session.scalars(select(ToolAudit).where(ToolAudit.tool == "trigger_refund")).all()
    assert rows[0].outcome == "capped"


def test_replay_is_journaled_as_replayed(db_session):
    """Un rejeu laisse une trace distincte du premier appel : sans elle, un pic
    de retries (donc un problème réseau ou un agent qui boucle) serait
    invisible."""
    trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        15.0,
        "retard",
        intent_token=refund_token(db_session, "O-2024-0101", "C-marc-dubois", 15.0, "retard"),
    )
    trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        15.0,
        "retard",
        intent_token=refund_token(db_session, "O-2024-0101", "C-marc-dubois", 15.0, "retard"),
    )
    outcomes = db_session.scalars(
        select(ToolAudit.outcome).where(ToolAudit.tool == "trigger_refund")
    ).all()
    assert outcomes == ["ok", "replayed"]


def test_journal_masks_pii_in_arguments(db_session):
    """Une adresse de livraison est une donnée personnelle : le journal
    d'actions ne doit pas devenir la copie non filtrée que tout le reste du
    système évite."""
    update_shipping_address(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        {"line1": "12 rue des Lilas", "city": "Lyon", "card": "4111 1111 1111 1111"},
    )
    row = db_session.scalars(
        select(ToolAudit).where(ToolAudit.tool == "update_shipping_address")
    ).first()
    assert row is not None
    assert "4111 1111 1111 1111" not in row.arguments


def test_idempotency_key_is_unique_in_the_database(db_session):
    """La garantie est portée par la base, pas par une vérification applicative
    préalable — celle-ci perdrait la course entre deux appels concurrents, qui
    est exactement le scénario du retry."""
    from sqlalchemy.exc import IntegrityError

    from velmo.db import ToolAudit as TA

    db_session.add(TA(id="ta-1", user_id="u", tool="t", tool_class="I", idempotency_key="dup"))
    db_session.commit()
    db_session.add(TA(id="ta-2", user_id="u", tool="t", tool_class="I", idempotency_key="dup"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_guardrail_escalation_is_deduplicated_within_a_turn(db_session):
    """Cas réel du chemin agent : les garde-fous escaladent avec un motif
    construit depuis la catégorie. Deux blocages de même catégorie dans une même
    conversation ne doivent pas ouvrir deux tickets identiques."""
    from velmo.tools import escalate_to_human as escalate

    def _count() -> int:
        return len(
            db_session.scalars(
                select(Escalation).where(Escalation.customer_id == "C-karim-benali")
            ).all()
        )

    before = _count()
    escalate(db_session, "C-karim-benali", "garde-fou violence (entrée)", channel="support")
    escalate(db_session, "C-karim-benali", "garde-fou violence (entrée)", channel="support")
    assert _count() == before + 1


def test_escalations_on_different_channels_are_distinct(db_session):
    """Le canal fait partie de la clé : un risque humain et un risque technique
    vont à deux destinataires différents, donc ce sont deux actions."""

    def _count() -> int:
        return len(
            db_session.scalars(
                select(Escalation).where(Escalation.customer_id == "C-emma-roux")
            ).all()
        )

    before = _count()
    escalate_to_human(db_session, "C-emma-roux", "fuite confirmee", channel="support")
    escalate_to_human(db_session, "C-emma-roux", "fuite confirmee", channel="security")
    assert _count() == before + 2
