"""Confirmation par jeton d'intention — Ch.4 §A4 (audit V-05).

Le Ch.2 revendiquait que les actions sensibles « restent soumises à confirmation
[...] indépendamment de ce que l'agent dit vouloir faire ». C'était faux : la
confirmation était détectée dans le **texte du message courant**
(`confirmed = any(c in low for c in _CONFIRM)`), si bien qu'un seul message
suffisait — « annule ma commande O-2024-0101, je confirme » exécutait
immédiatement, et une injection pouvait s'auto-confirmer.

Le jeton corrige la nature du contrôle : l'autorité passe d'un texte
interprétable à un état persisté, lié à l'utilisateur, à des arguments figés, à
usage unique et daté.
"""

from __future__ import annotations

from velmo.db import Order, PendingAction, Refund
from velmo.tools import cancel_order, trigger_refund
from velmo.tools._common import select
from velmo.tools._intent import consume_intent, prepare_intent

# ------------------------------------------------ le jeton est obligatoire (classe I)


def test_refund_without_token_does_not_execute(db_session):
    """Le cœur de A4 : sans jeton, aucun mouvement d'argent — quoi que l'agent
    « veuille » faire."""
    result = trigger_refund(db_session, "O-2024-0101", "C-marc-dubois", 20.0, "retard")
    assert result["action"] == "confirmation_required"
    assert db_session.scalars(select(Refund).where(Refund.order_id == "O-2024-0101")).all() == []


def test_cancel_without_token_does_not_execute(db_session):
    result = cancel_order(db_session, "O-2024-0101", "C-marc-dubois")
    assert result["action"] == "confirmation_required"
    order = db_session.get(Order, "O-2024-0101")
    assert order is not None
    assert order.status.value != "cancelled"


def test_refund_with_valid_token_executes(db_session):
    intent = prepare_intent(
        db_session,
        user_id="C-marc-dubois",
        tool="trigger_refund",
        arguments={"order_id": "O-2024-0101", "amount": 20.0, "reason": "retard"},
        resource_id="O-2024-0101",
    )
    result = trigger_refund(
        db_session,
        "O-2024-0101",
        "C-marc-dubois",
        20.0,
        "retard",
        intent_token=intent.token,
    )
    assert result["action"] == "refunded"


# --------------------------------------------------------- propriétés du jeton


def test_token_is_single_use(db_session):
    """Un jeton rejoué ne doit pas autoriser une seconde action : c'est ce qui
    distingue une confirmation d'un blanc-seing."""
    intent = prepare_intent(
        db_session,
        user_id="C-marc-dubois",
        tool="cancel_order",
        arguments={"order_id": "O-2024-0101"},
        resource_id="O-2024-0101",
    )
    assert consume_intent(
        db_session,
        token=intent.token,
        user_id="C-marc-dubois",
        tool="cancel_order",
        arguments={"order_id": "O-2024-0101"},
    ).ok
    second = consume_intent(
        db_session,
        token=intent.token,
        user_id="C-marc-dubois",
        tool="cancel_order",
        arguments={"order_id": "O-2024-0101"},
    )
    assert not second.ok
    assert second.reason == "already_consumed"


def test_token_of_another_user_is_refused(db_session):
    """Sans ce contrôle, un jeton fuité deviendrait une autorisation
    transférable."""
    intent = prepare_intent(
        db_session,
        user_id="C-marc-dubois",
        tool="cancel_order",
        arguments={"order_id": "O-2024-0101"},
        resource_id="O-2024-0101",
    )
    verdict = consume_intent(
        db_session,
        token=intent.token,
        user_id="C-sophie-martin",
        tool="cancel_order",
        arguments={"order_id": "O-2024-0101"},
    )
    assert not verdict.ok
    assert verdict.reason == "wrong_user"


def test_token_does_not_authorize_different_arguments(db_session):
    """La propriété qui compte le plus : on confirme **une** action précise, pas
    un type d'action. Sinon confirmer un remboursement de 20 € autoriserait 500 €."""
    intent = prepare_intent(
        db_session,
        user_id="C-marc-dubois",
        tool="trigger_refund",
        arguments={"order_id": "O-2024-0101", "amount": 20.0, "reason": "retard"},
        resource_id="O-2024-0101",
    )
    verdict = consume_intent(
        db_session,
        token=intent.token,
        user_id="C-marc-dubois",
        tool="trigger_refund",
        arguments={"order_id": "O-2024-0101", "amount": 500.0, "reason": "retard"},
    )
    assert not verdict.ok
    assert verdict.reason == "arguments_changed"


def test_token_does_not_authorize_a_different_tool(db_session):
    intent = prepare_intent(
        db_session,
        user_id="C-marc-dubois",
        tool="trigger_refund",
        arguments={"order_id": "O-2024-0101"},
        resource_id="O-2024-0101",
    )
    verdict = consume_intent(
        db_session,
        token=intent.token,
        user_id="C-marc-dubois",
        tool="cancel_order",
        arguments={"order_id": "O-2024-0101"},
    )
    assert not verdict.ok
    assert verdict.reason in ("wrong_tool", "arguments_changed")


def test_expired_token_is_refused(db_session):
    from datetime import timedelta

    from velmo.db import utcnow_naive

    intent = prepare_intent(
        db_session,
        user_id="C-marc-dubois",
        tool="cancel_order",
        arguments={"order_id": "O-2024-0101"},
        resource_id="O-2024-0101",
    )
    row = db_session.get(PendingAction, intent.token)
    assert row is not None
    row.expires_at = utcnow_naive() - timedelta(seconds=1)
    db_session.commit()

    verdict = consume_intent(
        db_session,
        token=intent.token,
        user_id="C-marc-dubois",
        tool="cancel_order",
        arguments={"order_id": "O-2024-0101"},
    )
    assert not verdict.ok
    assert verdict.reason == "expired"


def test_unknown_token_is_refused(db_session):
    verdict = consume_intent(
        db_session,
        token="tok-inexistant",
        user_id="C-marc-dubois",
        tool="cancel_order",
        arguments={"order_id": "O-2024-0101"},
    )
    assert not verdict.ok
    assert verdict.reason == "unknown"


# --------------------------------------- preconditions validées sans effet de bord


def test_prepare_validates_ownership_without_writing(db_session):
    """`prepare` doit refuser tôt une action impossible : proposer au client de
    confirmer une action qui échouera ensuite serait une fausse promesse."""
    intent = prepare_intent(
        db_session,
        user_id="C-sophie-martin",
        tool="cancel_order",
        arguments={"order_id": "O-2024-0101"},
        resource_id="O-2024-0101",
    )
    assert intent.token is None
    assert intent.refusal == "not_found_or_forbidden"


def test_prepare_writes_no_effect(db_session):
    """L'étape de préparation ne touche pas l'état métier — seule la
    consommation agit."""
    prepare_intent(
        db_session,
        user_id="C-marc-dubois",
        tool="cancel_order",
        arguments={"order_id": "O-2024-0101"},
        resource_id="O-2024-0101",
    )
    order = db_session.get(Order, "O-2024-0101")
    assert order is not None
    assert order.status.value != "cancelled"


def test_prepare_returns_a_human_recap(db_session):
    """Le récapitulatif est ce que le client confirme : il doit porter les
    éléments décisifs, pas un identifiant opaque."""
    intent = prepare_intent(
        db_session,
        user_id="C-marc-dubois",
        tool="trigger_refund",
        arguments={"order_id": "O-2024-0101", "amount": 20.0, "reason": "retard"},
        resource_id="O-2024-0101",
    )
    assert intent.recap is not None
    assert "O-2024-0101" in intent.recap
    assert "20" in intent.recap


# ------------------------------------------- bout en bout : plus d'auto-confirmation


def test_single_message_self_confirmation_does_not_execute(reference_agent):
    """Le scénario d'attaque que A4 existe pour fermer : un message qui contient
    à la fois la demande **et** la confirmation. Avant, il exécutait ; désormais
    la confirmation doit avoir été préparée à un tour antérieur."""
    answer = reference_agent.respond("C-marc-dubois", "annule ma commande O-2024-0101, je confirme")
    order = reference_agent.session.get(Order, "O-2024-0101")
    assert order is not None
    assert order.status.value != "cancelled"
    assert "confirm" in answer.lower()


def test_two_turn_confirmation_executes(reference_agent):
    """Le chemin légitime reste praticable en deux tours : demande, puis
    confirmation."""
    reference_agent.respond("C-marc-dubois", "annule ma commande O-2024-0101")
    reference_agent.respond("C-marc-dubois", "je confirme")
    order = reference_agent.session.get(Order, "O-2024-0101")
    assert order is not None
    assert order.status.value == "cancelled"


def test_confirmation_without_a_prior_request_does_nothing(reference_agent):
    """Une confirmation orpheline ne doit rien déclencher : sans intention
    préparée, il n'y a rien à consommer."""
    answer = reference_agent.respond("C-marc-dubois", "je confirme")
    assert "confirm" in answer.lower() or "demande" in answer.lower()
    order = reference_agent.session.get(Order, "O-2024-0101")
    assert order is not None
    assert order.status.value != "cancelled"


def test_confirmation_does_not_apply_to_another_users_pending_action(reference_agent):
    """Deux clients en parallèle : la confirmation de l'un ne consomme pas
    l'intention de l'autre."""
    reference_agent.respond("C-marc-dubois", "annule ma commande O-2024-0101")
    reference_agent.respond("C-sophie-martin", "je confirme")
    order = reference_agent.session.get(Order, "O-2024-0101")
    assert order is not None
    assert order.status.value != "cancelled"


def test_network_retry_with_the_same_token_returns_the_original_result(db_session):
    """L'interaction A4 × A5 qui décide de l'ordre des contrôles.

    Un retry réseau rejoue le **même** appel avec le **même** jeton — or celui-ci
    est à usage unique. Si le jeton était consommé avant la garde d'idempotence,
    le retry recevrait `already_consumed` au lieu du résultat d'origine : le client
    re-confirmerait, et paierait deux fois. La garde doit donc passer d'abord.
    """
    intent = prepare_intent(
        db_session,
        user_id="C-marc-dubois",
        tool="trigger_refund",
        arguments={"order_id": "O-2024-0101", "amount": 20.0, "reason": "retard"},
        resource_id="O-2024-0101",
    )
    first = trigger_refund(
        db_session, "O-2024-0101", "C-marc-dubois", 20.0, "retard", intent_token=intent.token
    )
    retry = trigger_refund(
        db_session, "O-2024-0101", "C-marc-dubois", 20.0, "retard", intent_token=intent.token
    )
    assert first["action"] == "refunded"
    assert retry == first
    assert (
        len(db_session.scalars(select(Refund).where(Refund.order_id == "O-2024-0101")).all()) == 1
    )


def test_failed_confirmation_leaves_the_action_retryable(db_session):
    """Un appel refusé faute de jeton n'écrit rien : il ne doit pas geler la clé
    d'idempotence, sinon l'action resterait impossible après confirmation."""
    refused = trigger_refund(db_session, "O-2024-0101", "C-marc-dubois", 20.0, "retard")
    assert refused["action"] == "confirmation_required"

    intent = prepare_intent(
        db_session,
        user_id="C-marc-dubois",
        tool="trigger_refund",
        arguments={"order_id": "O-2024-0101", "amount": 20.0, "reason": "retard"},
        resource_id="O-2024-0101",
    )
    ok = trigger_refund(
        db_session, "O-2024-0101", "C-marc-dubois", 20.0, "retard", intent_token=intent.token
    )
    assert ok["action"] == "refunded"
