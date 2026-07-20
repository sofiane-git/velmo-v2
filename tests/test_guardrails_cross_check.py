from __future__ import annotations

from velmo.guardrails.cross_check import find_foreign_identifiers


def test_finds_order_number_not_belonging_to_current_user() -> None:
    own_facts = {"order_number": "O-2024-0101"}
    text = "Votre commande O-2024-0101 est en cours, comme celle de M. Dubois O-2024-0107."
    foreign = find_foreign_identifiers(text, own_facts)
    assert "O-2024-0107" in foreign
    assert "O-2024-0101" not in foreign


def test_no_false_positive_when_only_own_identifiers_present() -> None:
    own_facts = {"order_number": "O-2024-0101", "contract_number": "C-8841"}
    text = "Votre commande O-2024-0101 (contrat C-8841) est confirmée."
    assert find_foreign_identifiers(text, own_facts) == []
