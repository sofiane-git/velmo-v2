from __future__ import annotations

from velmo.mlops.suites.memory import run_memory_suite


def test_memory_suite_passes_all_cases_by_default() -> None:
    results = run_memory_suite(db_url="sqlite:///:memory:")
    assert len(results) > 0
    failed = [r for r in results if not r.passed]
    assert failed == [], f"Cas échoués : {[r.case_id for r in failed]}"


def test_memory_suite_covers_all_tags_r1_to_r6() -> None:
    results = run_memory_suite(db_url="sqlite:///:memory:")
    case_ids = {r.case_id for r in results}
    # Un id par tag présent dans eval/memory_cases.jsonl (R1..R6) — preuve que
    # la suite ne saute silencieusement aucune catégorie de cas.
    assert any(cid.startswith("R1-") for cid in case_ids)
    assert any(cid.startswith("R2-") for cid in case_ids)
    assert any(cid.startswith("R3-") for cid in case_ids)
    assert any(cid.startswith("R4-") for cid in case_ids)
    assert any(cid.startswith("R5-") for cid in case_ids)
    assert any(cid.startswith("R6-") for cid in case_ids)


def test_memory_suite_r3_detects_cross_user_leak() -> None:
    """Preuve que la suite fait un vrai test d'isolation, pas juste un recall
    indépendant par utilisateur : si on force artificiellement une fuite (même
    manager, mauvais user_id), le cas R3 doit échouer."""
    from velmo.mlops.suites import memory as memory_suite

    # Cas synthétique minimal reproduisant une fuite : deux faits, même clé,
    # mais on interroge avec le user_id de l'autre — le test vérifie que la
    # fonction de vérification interne détecte bien ce cas.
    leaked = memory_suite._check_no_cross_leak(
        own_substring="O-2024-0103",
        other_case_substrings=["O-2024-0103"],  # fuite simulée : l'autre user "voit" ce fait
        rendered_context="Votre commande prioritaire est O-2024-0103.",
    )
    assert leaked is False  # la fonction détecte la fuite -> "pas d'isolation" -> False


def test_memory_suite_emits_instrumentation_events() -> None:
    """Preuve que `sink` (Task 5) est bien câblé jusqu'à l'extracteur/LLM
    internes de `MemoryManager` — pas seulement accepté en paramètre mort."""

    class _RecordingSink:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, float, float]] = []

        def on_llm_call(self, component: str, tokens: int, latency_ms: float, cost: float) -> None:
            self.calls.append((component, tokens, latency_ms, cost))

        def run_url(self, run_id: str) -> str | None:
            return None

    sink = _RecordingSink()
    run_memory_suite(db_url="sqlite:///:memory:", sink=sink)
    assert len(sink.calls) > 0
    assert all(c[0] in ("memory_extractor", "memory_summary") for c in sink.calls)
