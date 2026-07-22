from __future__ import annotations

import json
from pathlib import Path

from velmo.memory.extractor import (
    ExtractionResult,
    LLMExtractor,
    RuleBasedExtractor,
)


def _keys(result: ExtractionResult) -> set[str]:
    return {f.key for f in result.facts}


def test_shoe_size_extracted():
    result = RuleBasedExtractor().extract(
        "Je porte toujours la taille L pour les maillots.", "Note : taille L."
    )
    assert "shoe_size" in _keys(result)
    fact = next(f for f in result.facts if f.key == "shoe_size")
    assert fact.value == "L"
    assert fact.confidence >= 0.7


def test_shoe_size_numeric_pointure_extracted_from_assistant_trigger():
    """La pointure numerique est capturee meme quand le mot declencheur
    ("pointure") n'apparait que dans la reformulation de l'assistant."""
    result = RuleBasedExtractor().extract(
        "Pour info je fais du 42 en maillot.", "Note : pointure 42."
    )
    fact = next(f for f in result.facts if f.key == "shoe_size")
    assert fact.value == "42"


def test_clubs_extracted():
    result = RuleBasedExtractor().extract("Mes clubs preferes sont l'OM et le Bresil.", "Note.")
    assert "clubs" in _keys(result)
    fact = next(f for f in result.facts if f.key == "clubs")
    assert "OM" in fact.value


def test_segment_revendeur_extracted():
    result = RuleBasedExtractor().extract(
        "Je suis revendeur, je commande souvent en volume.", "Note."
    )
    fact = next(f for f in result.facts if f.key == "segment")
    assert fact.value == "revendeur"


def test_address_mode_tutoiement_extracted():
    result = RuleBasedExtractor().extract("Tutoie-moi s'il te plait.", "D'accord.")
    fact = next(f for f in result.facts if f.key == "address_mode")
    assert fact.value == "tu"


def test_contract_number_extracted():
    result = RuleBasedExtractor().extract("Je suis client pro, contrat #C-8841.", "Note.")
    fact = next(f for f in result.facts if f.key == "contract_number")
    assert fact.value == "C-8841"


def test_order_number_extracted_without_trigger_word():
    result = RuleBasedExtractor().extract("Ma commande prioritaire est O-2024-0101.", "Note.")
    fact = next(f for f in result.facts if f.key == "order_number")
    assert fact.value == "O-2024-0101"


def test_address_extracted():
    result = RuleBasedExtractor().extract("Mon adresse de livraison est 12 rue des Lilas.", "Note.")
    fact = next(f for f in result.facts if f.key == "address")
    assert fact.value == "12 rue des Lilas"


def test_dispute_extracted():
    result = RuleBasedExtractor().extract("Le maillot recu est un faux, je conteste.", "Compris.")
    fact = next(f for f in result.facts if f.key == "dispute")
    assert fact.type == "dispute"


def test_rulebased_never_yields_procedures():
    result = RuleBasedExtractor().extract("Je porte la taille L.", "Note.")
    assert result.procedures == []


def test_small_talk_yields_nothing():
    result = RuleBasedExtractor().extract("Merci, bonne journee !", "Avec plaisir.")
    assert result.facts == []
    assert result.procedures == []


class _StubJSONLLM:
    """LLM double : renvoie un JSON figé, quel que soit le prompt."""

    def __init__(self, payload: str) -> None:
        self._payload = payload

    def invoke(self, system: str, context: str, message: str) -> str:
        return self._payload


def test_llm_extractor_parses_facts_and_procedures():
    payload = (
        "Voici le résultat :\n"
        '{"facts": [{"key": "shoe_size", "value": "L", '
        '"type": "preference", "confidence": 0.9}], '
        '"procedures": [{"trigger": "refund_offer", '
        '"rule": "Proposer un bon de 10%.", "confidence": 0.8}]}'
    )
    result = LLMExtractor(_StubJSONLLM(payload)).extract("Je fais du L.", "Noté.")
    assert result.facts[0].key == "shoe_size"
    assert result.procedures[0].trigger == "refund_offer"


def test_llm_extractor_returns_empty_on_garbage():
    result = LLMExtractor(_StubJSONLLM("pas du json du tout")).extract("Salut", "Salut")
    assert result.facts == []
    assert result.procedures == []


def test_get_extractor_falls_back_to_rule_based_without_anthropic_foundry_config(
    monkeypatch,
) -> None:
    from velmo.memory.extractor import RuleBasedExtractor, get_extractor

    monkeypatch.delenv("ANTHROPIC_FOUNDRY_ENDPOINT", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(get_extractor(), RuleBasedExtractor)


def test_get_extractor_uses_llm_extractor_when_anthropic_foundry_configured(monkeypatch) -> None:
    from velmo.memory.extractor import LLMExtractor, get_extractor

    monkeypatch.setenv("ANTHROPIC_FOUNDRY_ENDPOINT", "https://fake.services.ai.azure.com/anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    assert isinstance(get_extractor(), LLMExtractor)


def test_memory_confidence_cases_fixture_exists_and_well_formed() -> None:
    path = Path(__file__).resolve().parent.parent / "eval" / "memory_confidence_cases.jsonl"
    assert path.exists()
    cases = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(cases) >= 25  # "une trentaine" — tolérance basse pour ne pas figer un compte exact
    for case in cases:
        assert case["decision"] in ("retain", "discard")
        assert "message" in case and "id" in case
