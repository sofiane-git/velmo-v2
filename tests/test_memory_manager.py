from __future__ import annotations

from velmo.memory import MemoryManager


def _mm(**kwargs):
    kwargs.setdefault("db_url", "sqlite:///:memory:")
    return MemoryManager(**kwargs)


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
    assert removed >= 1
    assert "rue des Lilas" not in mm.read("u6", "Mon adresse ?").render()


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
    assert removed >= 1
    assert mm.inspect("fp")["procedures"] == []
