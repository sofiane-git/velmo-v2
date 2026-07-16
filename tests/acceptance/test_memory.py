"""Tests d'acceptance — chantier Mémoire (contexte boutique collector)."""

from __future__ import annotations

from velmo.memory import MemoryManager


def test_recall_over_30_turns():
    # Critère R1 : info du 1er tour restituée après 30+ tours.
    mm = MemoryManager()
    user = "acc-recall"
    mm.write(user, "Ma commande prioritaire est O-2024-0101.", "C'est noté.")
    for i in range(30):
        mm.write(user, f"Question de suivi {i} sur un maillot.", f"Réponse {i}.")

    rendered = mm.read(user, "Quelle était ma commande prioritaire ?").render()
    assert "O-2024-0101" in rendered


def test_cross_session_persistence():
    # Critère R2 : pointure, clubs et segment retrouvés une session plus tard.
    session1 = MemoryManager()
    session1.remember_fact("acc-marc", "pointure", "L")
    session1.remember_fact("acc-marc", "clubs", "OM et Brésil")
    session1.remember_fact("acc-marc", "segment", "revendeur")

    session2 = MemoryManager()  # nouvelle session, même client
    rendered = session2.read("acc-marc", "Tu te souviens de moi ?").render()
    assert "L" in rendered
    assert "OM" in rendered
    assert "revendeur" in rendered


def test_isolation_between_customers():
    # Critère R3 : Marc ne voit jamais les commandes de Sophie.
    mm = MemoryManager()
    mm.remember_fact("acc-marc", "commande", "O-2024-0103")
    mm.remember_fact("acc-sophie", "commande", "O-2024-0107")

    rendered_sophie = mm.read("acc-sophie", "Mes commandes ?").render()
    assert "O-2024-0107" in rendered_sophie
    assert "O-2024-0103" not in rendered_sophie


def test_right_to_be_forgotten():
    # Critère R5 : « oublie mon adresse » supprime effectivement l'information.
    mm = MemoryManager()
    user = "acc-forget"
    mm.write(user, "Mon adresse de livraison est 12 rue des Lilas.", "C'est noté.")

    assert "rue des Lilas" in mm.read(user, "Mon adresse ?").render()

    removed = mm.forget(user, "adresse")
    assert removed.count >= 1
    assert "rue des Lilas" not in mm.read(user, "Mon adresse ?").render()
