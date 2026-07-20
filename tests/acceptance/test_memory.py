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
    # DB isolée : contrairement à test_cross_session_persistence, ce test n'utilise
    # qu'une seule instance mm et n'a pas besoin du fichier SQLite persistant partagé.
    # Sans ça, le tombstone posé par forget() survit au fichier var/velmo_memory.db
    # d'une exécution pytest à l'autre et bloque la ré-écriture du fait au run suivant.
    mm = MemoryManager(db_url="sqlite:///:memory:")
    user = "acc-forget"
    mm.write(user, "Mon adresse de livraison est 12 rue des Lilas.", "C'est noté.")

    assert "rue des Lilas" in mm.read(user, "Mon adresse ?").render()

    removed = mm.forget(user, "adresse")
    assert removed.count >= 1
    assert "rue des Lilas" not in mm.read(user, "Mon adresse ?").render()


def test_clear_session_resets_history_but_keeps_facts():
    # `clear_session` (équivalent `/clear`) : l'historique de conversation
    # disparaît, la mémoire long terme (faits) reste intacte — à la
    # différence de `forget_all` (droit à l'oubli total).
    mm = MemoryManager()
    user = "acc-clear-session"
    mm.remember_fact(user, "shoe_size", "L")
    # Message générique, sans entité extractible (numéro de commande, etc.) :
    # ne doit peupler que l'historique de conversation, pas un fait long terme.
    mm.write(user, "Les maillots vintage sont-ils garantis ?", "Oui, un an sur les défauts.")

    before = mm.read(user, "Rappel ?").render()
    assert "garantis" in before
    assert "L" in before

    mm.clear_session(user)

    after = mm.read(user, "Rappel ?").render()
    assert "garantis" not in after
    assert "L" in after
