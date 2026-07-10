from __future__ import annotations

from velmo.memory.extractor import RuleBasedExtractor


def _keys(facts):
    return {f.key for f in facts}


def test_shoe_size_extracted():
    facts = RuleBasedExtractor().extract(
        "Je porte toujours la taille L pour les maillots.", "Note : taille L."
    )
    assert "shoe_size" in _keys(facts)
    fact = next(f for f in facts if f.key == "shoe_size")
    assert fact.value == "L"
    assert fact.confidence >= 0.7


def test_clubs_extracted():
    facts = RuleBasedExtractor().extract("Mes clubs preferes sont l'OM et le Bresil.", "Note.")
    assert "clubs" in _keys(facts)
    fact = next(f for f in facts if f.key == "clubs")
    assert "OM" in fact.value


def test_segment_revendeur_extracted():
    facts = RuleBasedExtractor().extract(
        "Je suis revendeur, je commande souvent en volume.", "Note."
    )
    fact = next(f for f in facts if f.key == "segment")
    assert fact.value == "revendeur"


def test_address_mode_tutoiement_extracted():
    facts = RuleBasedExtractor().extract("Tutoie-moi s'il te plait.", "D'accord.")
    fact = next(f for f in facts if f.key == "address_mode")
    assert fact.value == "tu"


def test_contract_number_extracted():
    facts = RuleBasedExtractor().extract("Je suis client pro, contrat #C-8841.", "Note.")
    fact = next(f for f in facts if f.key == "contract_number")
    assert fact.value == "C-8841"


def test_order_number_extracted_without_trigger_word():
    facts = RuleBasedExtractor().extract("Ma commande prioritaire est O-2024-0101.", "Note.")
    fact = next(f for f in facts if f.key == "order_number")
    assert fact.value == "O-2024-0101"


def test_address_extracted():
    facts = RuleBasedExtractor().extract("Mon adresse de livraison est 12 rue des Lilas.", "Note.")
    fact = next(f for f in facts if f.key == "address")
    assert fact.value == "12 rue des Lilas"


def test_dispute_extracted():
    facts = RuleBasedExtractor().extract("Le maillot recu est un faux, je conteste.", "Compris.")
    fact = next(f for f in facts if f.key == "dispute")
    assert fact.type == "dispute"


def test_small_talk_yields_nothing():
    facts = RuleBasedExtractor().extract("Merci, bonne journee !", "Avec plaisir.")
    assert facts == []
