"""Sélection de l'article dans `update_order_item` — défaut relevé au Ch.4 §État.

L'outil modifiait `order.items[0]` : le **premier** article, sans paramètre de
sélection. Sur une commande à plusieurs articles, il modifiait donc un article que
le client n'avait pas désigné — silencieusement, en renvoyant « c'est fait ».

Le défaut était invisible parce que **toutes** les commandes du jeu de données
sont mono-article. C'est le genre de bug qu'aucun test ne pouvait attraper sans
une fixture qui sort du cas nominal ; d'où la commande à deux articles ci-dessous.

Contrat retenu : `item_id` explicite quand il y a une ambiguïté. Omis, l'outil
opère sur l'unique article s'il n'y en a qu'un, et **refuse** au-delà en demandant
lequel — jamais de choix implicite.
"""

from __future__ import annotations

import pytest

from velmo.db import Order, OrderItem, Size
from velmo.tools import update_order_item
from velmo.tools._common import select


@pytest.fixture
def multi_item_order(db_session):
    """Commande à deux articles de tailles distinctes, pour que « quel article a
    changé ? » soit une question à réponse observable."""
    order = db_session.get(Order, "O-2024-0101")
    assert order is not None
    db_session.add(
        OrderItem(
            id="oi-0101-b",
            order_id="O-2024-0101",
            variant_id="v-om-1993-L",
            size=Size.M,
            unit_price=180,
        )
    )
    db_session.commit()
    return order


def _sizes(db_session) -> dict[str, str]:
    items = db_session.scalars(select(OrderItem).where(OrderItem.order_id == "O-2024-0101")).all()
    return {item.id: item.size.value for item in items}


# ---------------------------------------------------- le cas qui était silencieux


def test_multi_item_order_without_item_id_is_refused(db_session, multi_item_order):
    """Le cœur du correctif : plutôt que de modifier le premier article au
    hasard, l'outil refuse et demande lequel."""
    before = _sizes(db_session)
    result = update_order_item(db_session, "O-2024-0101", "C-marc-dubois", "XL")
    assert result["action"] == "item_selection_required"
    # Les articles sont listés pour que le client puisse trancher.
    assert {item["item_id"] for item in result["items"]} == set(before)
    assert _sizes(db_session) == before  # rien n'a bougé


def test_multi_item_order_with_item_id_changes_only_that_item(db_session, multi_item_order):
    before = _sizes(db_session)
    result = update_order_item(
        db_session, "O-2024-0101", "C-marc-dubois", "XL", item_id="oi-0101-b"
    )
    assert result["action"] == "updated"
    after = _sizes(db_session)
    assert after["oi-0101-b"] == "XL"
    assert after["oi-0101"] == before["oi-0101"]  # l'autre article est intact


# ------------------------------------------------------------ cas nominal préservé


def test_single_item_order_still_works_without_item_id(db_session):
    """Le cas courant reste sans friction : une commande mono-article n'a aucune
    ambiguïté à lever."""
    result = update_order_item(db_session, "O-2024-0101", "C-marc-dubois", "XL")
    assert result["action"] == "updated"
    assert _sizes(db_session)["oi-0101"] == "XL"


# --------------------------------------------------------- garde-fous du paramètre


def test_item_id_of_another_order_is_refused(db_session, multi_item_order):
    """Sans ce contrôle, `item_id` deviendrait un moyen de modifier l'article
    d'une commande — voire d'un client — non désignée par `order_id`."""
    result = update_order_item(db_session, "O-2024-0101", "C-marc-dubois", "XL", item_id="oi-0103")
    assert result["action"] == "item_not_found"
    other = db_session.get(OrderItem, "oi-0103")
    assert other is not None
    assert other.size.value != "XL"


def test_unknown_item_id_is_refused(db_session):
    result = update_order_item(
        db_session, "O-2024-0101", "C-marc-dubois", "XL", item_id="oi-inexistant"
    )
    assert result["action"] == "item_not_found"


def test_order_without_items_is_refused(db_session):
    """Cas limite : une commande sans ligne. L'ancien code aurait levé
    `IndexError` sur `items[0]`."""
    db_session.add(Order(id="O-2024-9999", customer_id="C-marc-dubois", total=0))
    db_session.commit()
    result = update_order_item(db_session, "O-2024-9999", "C-marc-dubois", "XL")
    assert result["action"] == "item_not_found"


# ------------------------------------------------ interaction avec l'idempotence


def test_item_id_is_part_of_the_idempotency_key(db_session, multi_item_order):
    """Deux articles différents de la même commande sont deux actions distinctes :
    la clé doit inclure `item_id`, sinon la seconde serait vue comme un rejeu de
    la première et ne s'appliquerait jamais."""
    update_order_item(db_session, "O-2024-0101", "C-marc-dubois", "XL", item_id="oi-0101")
    update_order_item(db_session, "O-2024-0101", "C-marc-dubois", "XL", item_id="oi-0101-b")
    after = _sizes(db_session)
    assert after["oi-0101"] == "XL"
    assert after["oi-0101-b"] == "XL"


# --------------------------------------------------------- chemin agent complet


def test_agent_asks_which_item_then_applies_the_designated_one(reference_agent):
    """Bout en bout : sans la propagation d'`item_id` jusqu'à l'agent, le refus de
    l'outil n'aurait aucune sortie utilisateur exploitable."""
    session = reference_agent.session
    session.add(
        OrderItem(
            id="oi-0101-b",
            order_id="O-2024-0101",
            variant_id="v-om-1993-L",
            size=Size.M,
            unit_price=180,
        )
    )
    session.commit()

    ask = reference_agent.respond(
        "C-marc-dubois", "je me suis trompé de taille sur O-2024-0101, mets du XL, je confirme"
    )
    assert "plusieurs articles" in ask
    assert "oi-0101-b" in ask

    reference_agent.respond(
        "C-marc-dubois",
        "changer la taille de O-2024-0101 article oi-0101-b en XL, je confirme",
    )
    items = session.scalars(select(OrderItem).where(OrderItem.order_id == "O-2024-0101")).all()
    sizes = {item.id: item.size.value for item in items}
    assert sizes["oi-0101-b"] == "XL"
    assert sizes["oi-0101"] == "L"  # l'article non désigné n'a pas bougé
