"""Tests d'acceptance R1–R6 — couverture complète de la mémoire (chantier 1)."""

from __future__ import annotations

from velmo.memory import MemoryManager
from velmo.memory.extractor import (
    ExtractedFact,
    ExtractedProcedure,
    ExtractionResult,
    RuleBasedExtractor,
)


class MockLLMExtractor:
    """Extracteur injectable : renvoie un résultat canné (faits + procédures)."""

    def __init__(self, result: ExtractionResult) -> None:
        self._result = result

    def extract(self, user_message: str, assistant_message: str) -> ExtractionResult:
        return self._result


def test_r1_info_tour1_au_tour30():
    # R1 : un fait extrait au tour 1 reste présent après 30 tours.
    mm = MemoryManager(db_url="sqlite:///:memory:")
    user = "r1"
    mm.write(user, "Ma commande prioritaire est O-2024-0101.", "C'est noté.")
    for i in range(30):
        mm.write(user, f"Suivi {i} sur un maillot.", f"Réponse {i}.")

    ctx = mm.read(user, "Quelle était ma commande prioritaire ?")
    assert ctx.facts.get("order_number") == "O-2024-0101"


def test_r2_persistence_inter_session(tmp_path):
    # R2 : faits ET procédures rechargés par un nouveau MemoryManager, même DB.
    db_url = f"sqlite:///{tmp_path / 'mem.db'}"
    canned = ExtractionResult(
        facts=[
            ExtractedFact(key="order_number", value="O-2024-0101", type="order", confidence=0.9)
        ],
        procedures=[
            ExtractedProcedure(
                trigger="refund_offer", rule="Proposer un bon d'achat de 10%.", confidence=0.9
            )
        ],
    )
    session1 = MemoryManager(db_url=db_url, extractor=MockLLMExtractor(canned))
    session1.write("marc", "Détails de commande.", "Ok.")

    session2 = MemoryManager(db_url=db_url, extractor=RuleBasedExtractor())
    ctx = session2.read("marc", "Tu te souviens de moi ?")
    assert ctx.facts.get("order_number") == "O-2024-0101"
    procs = session2.inspect("marc")["procedures"]
    assert any(p["trigger"] == "refund_offer" for p in procs)


def test_r3_isolation_user():
    # R3 : le contexte de B ne contient aucune clé de A.
    mm = MemoryManager(db_url="sqlite:///:memory:")
    mm.remember_fact("a", "order_number", "O-2024-0103")
    mm.remember_fact("b", "order_number", "O-2024-0107")

    ctx_b = mm.read("b", "Mes commandes ?")
    assert ctx_b.facts.get("order_number") == "O-2024-0107"
    assert "O-2024-0103" not in ctx_b.render()


def test_r4_compression_sans_perte():
    # R4 : la compression s'exécute réellement (résumé LLM présent dans le
    # contexte rendu) ET le fait critique survit dans ctx.facts.
    class _TagLLM:
        def invoke(self, system, context, message):
            return "RESUME_R4"

    mm = MemoryManager(
        db_url="sqlite:///:memory:", token_budget=20, keep_last_n_turns=1, llm=_TagLLM()
    )
    mm.write("r4", "Ma commande prioritaire est O-2024-0101.", "Noté.")
    for i in range(10):
        mm.write("r4", f"Question {i} sur un maillot très demandé.", f"Réponse {i}.")

    ctx = mm.read("r4", "Ma commande ?")
    rendered = ctx.render()
    assert "RESUME_R4" in rendered  # preuve que la compression a bien produit un résumé
    assert ctx.facts.get("order_number") == "O-2024-0101"


def test_r5_droit_oubli():
    # R5 : forget supprime le fait et caviarde l'historique.
    mm = MemoryManager(db_url="sqlite:///:memory:")
    mm.write("r5", "Ma commande prioritaire est O-2024-0101.", "Noté.")
    assert "O-2024-0101" in mm.read("r5", "Rappel ?").render()

    removed = mm.forget("r5", "order_number")
    assert removed.count >= 1
    assert "O-2024-0101" not in mm.read("r5", "Rappel ?").render()


def test_r6_inspection():
    # R6 : inspect() expose source_thread_id non nul et created_at ISO.
    mm = MemoryManager(db_url="sqlite:///:memory:")
    mm.write("r6", "Ma commande prioritaire est O-2024-0101.", "Noté.")

    facts = mm.inspect("r6")["facts"]
    order = next(f for f in facts if f["key"] == "order_number")
    assert order["source_thread_id"]  # non nul (issu de write)
    assert "T" in order["created_at"]  # ISO 8601
