from __future__ import annotations

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


def test_get_extractor_falls_back_to_rule_based_without_azure_async_config(monkeypatch) -> None:
    from velmo.memory.extractor import RuleBasedExtractor, get_extractor

    monkeypatch.delenv("AZURE_OPENAI_ASYNC_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ASYNC_API_KEY", raising=False)
    assert isinstance(get_extractor(), RuleBasedExtractor)


def test_get_extractor_uses_llm_extractor_when_azure_async_configured(monkeypatch) -> None:
    from velmo.memory.extractor import LLMExtractor, get_extractor

    monkeypatch.setenv("AZURE_OPENAI_ASYNC_ENDPOINT", "https://fake.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_ASYNC_API_KEY", "fake-key")
    assert isinstance(get_extractor(), LLMExtractor)
