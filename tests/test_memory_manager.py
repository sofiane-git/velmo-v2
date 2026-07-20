from __future__ import annotations

import threading

import pytest

from velmo.memory import MemoryManager, RemovedFact, RemovedProcedure


def _mm(**kwargs):
    kwargs.setdefault("db_url", "sqlite:///:memory:")
    return MemoryManager(**kwargs)


class _RaisingLLM:
    """LLM dont chaque appel echoue (simule un filtre de contenu Azure ou un timeout)."""

    def invoke(self, system: str, context: str, message: str) -> str:
        raise RuntimeError("content_filter blocked")


def test_write_extracts_order_number_fact_automatically():
    mm = _mm()
    mm.write("u1", "Ma commande prioritaire est O-2024-0101.", "Note.")
    ctx = mm.read("u1", "Quelle commande ?")
    assert ctx.facts.get("order_number") == "O-2024-0101"


def test_read_returns_all_raw_turns_under_budget():
    mm = _mm(token_budget=100000)
    mm.write("u2", "Bonjour", "Salut")
    mm.write("u2", "Une question", "Une reponse")
    ctx = mm.read("u2", "suite")
    assert len(ctx.history) == 4  # 2 echanges = 4 messages


def test_compression_triggers_past_budget_and_facts_still_surface():
    mm = _mm(token_budget=20, keep_last_n_turns=1)
    mm.write("u3", "Ma commande prioritaire est O-2024-0101.", "Note.")
    for i in range(10):
        mm.write(
            "u3",
            f"Question de suivi numero {i} sur un maillot tres demande.",
            f"Reponse detaillee {i}.",
        )
    ctx = mm.read("u3", "Quelle etait ma commande ?")
    rendered = ctx.render()
    assert "O-2024-0101" in rendered  # survit via FACT, meme hors fenetre brute
    assert len(ctx.history) < 22  # fenetre reellement retrecie (moins que 11 echanges * 2)


def test_compression_llm_failure_does_not_erase_current_turn_report():
    mm = _mm(token_budget=20, keep_last_n_turns=1, llm=_RaisingLLM())
    mm.write("u3b", "Ma commande prioritaire est O-2024-0101.", "Note.")
    for i in range(5):
        mm.write(
            "u3b",
            f"Question de suivi numero {i} sur un maillot tres demande.",
            f"Reponse detaillee {i}.",
        )
    report = mm.write(
        "u3b",
        "Ma commande prioritaire est O-2024-0202.",
        "C'est note.",
    )
    # L'echec du resume LLM (compression) ne doit pas ecraser le rapport du
    # tour courant, deja committe en base.
    assert any(f.key == "order_number" for f in report.facts_written)


def test_dispute_fact_creates_episode():
    mm = _mm()
    mm.write("u4", "Le maillot recu est un faux, je conteste.", "C'est note, je transmets.")
    inspected = mm.inspect("u4")
    assert inspected["episodic"]  # au moins un episode cree
    assert any(f["type"] == "dispute" for f in inspected["facts"])


def test_remember_fact_bypasses_extractor():
    mm = _mm()
    mm.remember_fact("u5", "pointure", "L")
    ctx = mm.read("u5", "Tu te souviens de moi ?")
    assert ctx.facts["pointure"] == "L"


def test_forget_removes_fact_and_redacts_history():
    mm = _mm()
    mm.write("u6", "Mon adresse de livraison est 12 rue des Lilas.", "C'est note.")
    assert "rue des Lilas" in mm.read("u6", "Mon adresse ?").render()
    removed = mm.forget("u6", "adresse")
    assert removed.count >= 1
    assert removed.facts == [RemovedFact(key="address", value="12 rue des Lilas")]
    assert "rue des Lilas" not in mm.read("u6", "Mon adresse ?").render()


def test_forget_all_wipes_facts_episodes_and_history_but_keeps_user_usable():
    mm = _mm()
    mm.write("u6b", "Mon adresse de livraison est 12 rue des Lilas.", "C'est note.")
    mm.write("u6b", "Le maillot recu est un faux, je conteste.", "C'est note, je transmets.")
    assert "rue des Lilas" in mm.read("u6b", "Mon adresse ?").render()
    inspected_before = mm.inspect("u6b")
    assert inspected_before["facts"]
    assert inspected_before["episodic"]

    removed = mm.forget_all("u6b")
    assert removed.count >= 1
    assert RemovedFact(key="address", value="12 rue des Lilas") in removed.facts
    assert removed.episodes  # l'episode de litige est bien dans le detail

    ctx = mm.read("u6b", "Mon adresse ?")
    assert "rue des Lilas" not in ctx.render()
    assert ctx.facts == {}
    inspected_after = mm.inspect("u6b")
    assert inspected_after["facts"] == []
    assert inspected_after["episodic"] == []

    # utilisateur toujours utilisable ensuite (pas de crash sur compte recree).
    mm.remember_fact("u6b", "pointure", "M")
    assert mm.read("u6b", "?").facts["pointure"] == "M"


def test_forget_all_does_not_touch_other_users():
    mm = _mm()
    mm.remember_fact("u6c", "commande", "O-2024-0109")
    mm.remember_fact("u6d", "commande", "O-2024-0110")
    mm.forget_all("u6c")
    assert mm.read("u6c", "?").facts == {}
    assert mm.read("u6d", "?").facts.get("commande") == "O-2024-0110"


def test_isolation_between_two_users():
    mm = _mm()
    mm.remember_fact("u7", "commande", "O-2024-0103")
    mm.remember_fact("u8", "commande", "O-2024-0107")
    assert "O-2024-0103" not in mm.read("u8", "?").render()
    assert "O-2024-0107" not in mm.read("u7", "?").render()


def test_inspect_shape():
    mm = _mm()
    mm.remember_fact("u9", "shoe_size", "L")
    result = mm.inspect("u9")
    assert set(result.keys()) == {"facts", "procedures", "episodic"}
    assert isinstance(result["facts"], list)
    fact = next(f for f in result["facts"] if f["key"] == "shoe_size")
    assert fact["value"] == "L"
    assert "created_at" in fact and "T" in fact["created_at"]  # ISO 8601
    assert "source_thread_id" in fact
    assert result["procedures"] == []


def test_compression_uses_llm_summary():
    class _TagLLM:
        def invoke(self, system, context, message):
            return "RESUME_LLM"

    mm = _mm(token_budget=20, keep_last_n_turns=1, llm=_TagLLM())
    mm.write("csum", "Ma commande prioritaire est O-2024-0101.", "Noté.")
    for i in range(10):
        mm.write("csum", f"Question {i} sur un maillot tres demande.", f"Reponse {i}.")

    ctx = mm.read("csum", "Rappel ?")
    rendered = ctx.render()
    assert "RESUME_LLM" in rendered  # le résumé provient bien du LLM
    assert ctx.facts.get("order_number") == "O-2024-0101"  # fait critique préservé


def test_compression_does_not_corrupt_dispute_fact_from_concatenated_block():
    mm = _mm(token_budget=20, keep_last_n_turns=1)
    dispute_msg = "Le maillot recu est un faux, je conteste."
    mm.write("cdispute", dispute_msg, "Nous traitons ce litige avec attention, je transmets.")
    mm.write(
        "cdispute",
        "Question de suivi numero 1 sur un maillot tres demande.",
        "Reponse 1.",
    )

    inspected = mm.inspect("cdispute")
    assert any(
        f["type"] == "dispute" and f["value"] == dispute_msg for f in inspected["facts"]
    )


def test_write_persists_procedures_from_extractor():
    from velmo.memory.extractor import ExtractedProcedure, ExtractionResult

    class _ProcExtractor:
        def extract(self, user_message, assistant_message):
            return ExtractionResult(
                procedures=[
                    ExtractedProcedure(
                        trigger="refund_offer", rule="Proposer un bon de 10%.", confidence=0.9
                    )
                ]
            )

    mm = _mm(extractor=_ProcExtractor())
    mm.write("up", "Peu importe.", "Ok.")
    procs = mm.inspect("up")["procedures"]
    assert any(p["trigger"] == "refund_offer" for p in procs)


def test_bind_user_is_noop_on_sqlite():
    # Sur SQLite, _bind_user ne doit rien exécuter et ne pas lever.
    mm = _mm()
    session = mm._Session()
    try:
        mm._bind_user(session, "any-user")  # ne doit pas lever
    finally:
        session.close()


def test_forget_removes_matching_procedure():
    from velmo.memory.extractor import ExtractedProcedure, ExtractionResult

    class _ProcExtractor:
        def extract(self, user_message, assistant_message):
            return ExtractionResult(
                procedures=[
                    ExtractedProcedure(
                        trigger="refund_offer", rule="Proposer un bon de 10%.", confidence=0.9
                    )
                ]
            )

    mm = _mm(extractor=_ProcExtractor())
    mm.write("fp", "Peu importe.", "Ok.")
    assert mm.inspect("fp")["procedures"]
    removed = mm.forget("fp", "refund")
    assert removed.count >= 1
    assert removed.procedures == [RemovedProcedure(trigger="refund_offer", rule="Proposer un bon de 10%.")]
    assert mm.inspect("fp")["procedures"] == []


def test_read_exposes_facts_detailed_with_confidence():
    mm = MemoryManager(db_url="sqlite:///:memory:")
    mm.write("fd1", "Ma taille est L, tu peux le noter ?", "Note.")
    ctx = mm.read("fd1", "Rappelle-moi ma taille.")
    assert any(f.key == "shoe_size" and f.value == "L" and f.confidence >= 0.7
               for f in ctx.facts_detailed)


def test_write_returns_report_with_facts_written():
    mm = MemoryManager(db_url="sqlite:///:memory:")
    report = mm.write("wr1", "Ma taille est L, tu peux le noter ?", "Note.")
    assert any(f.key == "shoe_size" and f.value == "L" for f in report.facts_written)
    assert report.episode_created is False


def test_write_reports_episode_created_on_dispute():
    mm = MemoryManager(db_url="sqlite:///:memory:")
    report = mm.write("wr2", "Le maillot recu est un faux, je conteste.", "Note, je transmets.")
    assert report.episode_created is True


def test_confidence_threshold_defaults_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_CONFIDENCE_THRESHOLD", "0.42")
    mm = MemoryManager(db_url="sqlite:///:memory:")
    assert mm.confidence_threshold == 0.42


def test_tombstone_blocks_late_extractor_write_after_forget() -> None:
    """Course R5 : un write extracteur arrivant après un forget() ne doit pas
    ressusciter la donnée effacée (voir doc §Course oubli ↔ écriture tardive)."""
    from velmo.memory.extractor import RuleBasedExtractor

    mm = MemoryManager(db_url="sqlite:///:memory:", extractor=RuleBasedExtractor())
    user = "acc-tombstone"
    mm.remember_fact(user, "shoe_size", "44")
    mm.forget(user, "pointure")

    # Simule une extraction tardive qui tenterait de réécrire la même clé.
    session = mm._Session()
    try:
        mm._bind_user(session, user)
        from velmo.memory.db import is_tombstoned

        assert is_tombstoned(session, user, "fact_key", "shoe_size") is True
    finally:
        session.close()

    mm._extract_and_persist(user, "je fais du 44", "noté")
    rendered = mm.read(user, "ma pointure ?").render()
    assert "44" not in rendered


def test_remember_fact_resolves_tombstone_and_unblocks_extractor() -> None:
    """Un `remember_fact` explicite (ré-)établit une donnée après un `forget()`
    antérieur : il doit lever le tombstone qu'il a laissé en place, sinon
    l'extracteur resterait bloqué à jamais sur cette clé alors qu'un fait
    vivant et à jour existe désormais (finding #2, review finale chantier 1)."""
    from velmo.memory.db import is_tombstoned
    from velmo.memory.extractor import RuleBasedExtractor

    mm = MemoryManager(db_url="sqlite:///:memory:", extractor=RuleBasedExtractor())
    user = "acc-tombstone-resolve"
    mm.remember_fact(user, "shoe_size", "XXL")
    mm.forget(user, "pointure")

    session = mm._Session()
    try:
        mm._bind_user(session, user)
        assert is_tombstoned(session, user, "fact_key", "shoe_size") is True
    finally:
        session.close()

    # Ré-établissement explicite : doit lever le tombstone posé par forget().
    mm.remember_fact(user, "shoe_size", "S")

    session = mm._Session()
    try:
        mm._bind_user(session, user)
        assert is_tombstoned(session, user, "fact_key", "shoe_size") is False
    finally:
        session.close()

    # L'extracteur peut désormais écrire à nouveau sur cette clé.
    mm._extract_and_persist(user, "Ma taille est L, tu peux le noter ?", "Noté.")
    rendered = mm.read(user, "ma pointure ?").render()
    assert "taille : L" in rendered


def test_concurrent_writes_do_not_corrupt_shared_checkpointer() -> None:
    """Le checkpointer LangGraph (SqliteSaver/PostgresSaver) est partagé pour
    la durée de vie de `MemoryManager` et enveloppe une seule connexion, non
    thread-safe : `write()` invoque le graphe depuis le thread appelant, tandis
    que l'extraction en arrière-plan (`_extract_and_persist`/`_maybe_compress`)
    tourne sur `_BACKGROUND_EXECUTOR` (finding #1, review finale chantier 1).
    Ce test fait écrire plusieurs threads "requête" concurrents sur la même
    instance/utilisateur et vérifie qu'aucune exception ne survient et
    qu'aucune écriture n'est perdue — la preuve que `_graph_lock` sérialise
    bien l'accès."""
    mm = MemoryManager(db_url="sqlite:///:memory:")
    user = "concurrent-user"
    # Amorce le thread actif avant la rafale concurrente : la création du
    # thread (`get_or_create_active_thread`) n'est pas elle-même protégée par
    # `_graph_lock` (elle ne touche pas le checkpointer) et n'est pas l'objet
    # de ce test.
    mm.write(user, "amorce", "ok")

    n = 12
    errors: list[BaseException] = []

    def _do_write(i: int) -> None:
        try:
            mm.write(user, f"message numero {i}", f"reponse numero {i}", background=True)
        except BaseException as exc:  # noqa: BLE001 - collecté pour assertion côté thread principal
            errors.append(exc)

    threads = [threading.Thread(target=_do_write, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"écriture(s) concurrente(s) en échec : {errors}"

    contents = [content for _, content in mm.read(user, "recap ?").history]
    for i in range(n):
        assert f"message numero {i}" in contents
        assert f"reponse numero {i}" in contents
