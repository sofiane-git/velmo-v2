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


def test_forget_all_purges_langgraph_checkpoints():
    # B1 (R5/art. 17) : l'effacement total supprime aussi le verbatim
    # conversationnel des checkpoints LangGraph, pas seulement les tables métier.
    mm = MemoryManager(db_url="sqlite:///:memory:")
    user = "acc-forget-checkpoints"
    mm.write(user, "Mon IBAN perso est FR76 3000 4000 0500 0600 0700 895.", "C'est noté.")

    from velmo.memory.db import list_threads

    with mm._Session() as session:
        thread_ids = [t.thread_id for t in list_threads(session, user)]
    assert thread_ids, "le tour doit avoir créé au moins un thread"
    # Le checkpoint contient bien le verbatim avant l'oubli.
    assert any(
        mm._checkpointer.get_tuple({"configurable": {"thread_id": tid}}) is not None
        for tid in thread_ids
    )

    mm.forget_all(user)

    # Après l'oubli total : plus aucun checkpoint pour ces threads.
    for tid in thread_ids:
        assert mm._checkpointer.get_tuple({"configurable": {"thread_id": tid}}) is None


def test_forget_all_tombstones_survive_cascade():
    # B2 : après forget_all, les tombstones doivent survivre — ils ne doivent
    # pas être emportés par la cascade FK de la suppression de l'utilisateur.
    # (La résurrection bloquée via l'extracteur en arrière-plan est couverte
    # déterministiquement par la Task 2 / D9-04, pas ici : un `remember_fact`
    # explicite, lui, lève délibérément le tombstone — comportement voulu.)
    mm = MemoryManager(db_url="sqlite:///:memory:")
    user = "acc-forget-tombstone"
    mm.remember_fact(user, "adresse", "12 rue des Lilas")

    mm.forget_all(user)

    from velmo.memory.db import is_tombstoned

    with mm._Session() as session:
        mm._bind_user(session, user)
        assert is_tombstoned(session, user, "fact_key", "adresse"), (
            "le tombstone doit survivre à l'effacement total"
        )


def test_forgotten_value_does_not_resurrect_via_episode():
    # D9-04 : après l'oubli d'un fait, un épisode écrit tardivement ne doit pas
    # réintroduire la valeur oubliée (la garde tombstone couvre aussi les épisodes,
    # pas seulement les faits/procédures à clé exacte).
    mm = MemoryManager(db_url="sqlite:///:memory:")
    user = "acc-episode-resurrect"
    secret = "O-2024-9999"
    mm.remember_fact(user, "commande_litige", secret)
    mm.forget_all(user)

    from velmo.memory.db import list_episodes

    with mm._Session() as session:
        mm._bind_user(session, user)
        added = mm._maybe_add_episode_guarded(session, user, f"Litige signalé : {secret}", None)
        session.commit()
    assert added is False, "un épisode reprenant une valeur sous tombstone doit être refusé"
    with mm._Session() as session:
        mm._bind_user(session, user)
        assert all(secret not in e.summary for e in list_episodes(session, user))


def test_purge_deletes_checkpoints_before_thread_headers():
    # D9-10 : la purge doit supprimer les checkpoints AVANT de committer la
    # suppression des en-têtes Thread, pour qu'un crash entre les deux n'orpheline
    # pas définitivement les checkpoints (les Thread ayant disparu, un re-run ne
    # les retrouverait jamais). On vérifie l'ordre en faisant échouer delete_thread.
    from velmo.memory import retention
    from velmo.memory.db import list_threads

    mm = MemoryManager(db_url="sqlite:///:memory:")
    user = "acc-purge-order"
    mm.write(user, "Commande O-2024-0001 à suivre.", "Noté.")
    with mm._Session() as session:
        thread_ids_before = [t.thread_id for t in list_threads(session, user)]
    assert thread_ids_before

    # Force le vieillissement pour que la purge cible le thread.
    from velmo.memory.db import utcnow
    from datetime import timedelta
    with mm._Session() as session:
        for t in list_threads(session, user):
            t.last_message_at = utcnow() - timedelta(days=200)
            session.add(t)
        session.commit()

    # delete_thread lève : si l'ordre est correct (checkpoints d'abord), l'exception
    # remonte AVANT le commit de suppression des en-têtes → les Thread survivent.
    original = mm._checkpointer.delete_thread

    def boom(_tid):
        raise RuntimeError("simulate crash during checkpoint purge")

    mm._checkpointer.delete_thread = boom  # type: ignore[method-assign]
    try:
        try:
            retention.purge_inactive_threads(mm, ttl_days=90)
        except RuntimeError:
            pass
    finally:
        mm._checkpointer.delete_thread = original  # type: ignore[method-assign]

    with mm._Session() as session:
        thread_ids_after = [t.thread_id for t in list_threads(session, user)]
    assert thread_ids_after == thread_ids_before, (
        "un crash pendant la purge des checkpoints ne doit pas laisser d'en-têtes "
        "Thread supprimés (donc de checkpoints orphelins)"
    )
