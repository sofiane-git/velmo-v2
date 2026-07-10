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
    assert inspected["facts"]["dispute"]


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
    assert result["facts"]["shoe_size"] == "L"
    assert result["procedures"] == []
