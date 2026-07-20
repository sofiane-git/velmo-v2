"""Cross-check `user_id` (G4, sortie) : le contrôle le plus précieux contre la
fuite inter-clients — un identifiant métier (commande, contrat) présent dans la
réponse qui n'appartient pas à l'utilisateur courant est masqué, indépendamment
de son format (la regex/Luhn de patterns.py ne détecte que des formats
structurés génériques, pas l'appartenance). Voir
conception_chantier2_guardrails.md §G4 en sortie : le cross-check user_id.
"""

from __future__ import annotations

from velmo.memory.entities import CONTRACT_RE, ORDER_RE

_IDENTIFIER_PATTERNS = (ORDER_RE, CONTRACT_RE)


def find_foreign_identifiers(text: str, own_facts: dict[str, str]) -> list[str]:
    """Identifiants (n° commande, n° contrat) présents dans `text` qui ne
    matchent aucune valeur de `own_facts` (les faits mémoire de l'utilisateur
    courant) — candidats à un masquage G4."""
    own_values = set(own_facts.values())
    found: list[str] = []
    for pattern in _IDENTIFIER_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if value not in own_values and value not in found:
                found.append(value)
    return found


def redact_foreign_identifiers(text: str, own_facts: dict[str, str]) -> str:
    """Masque chaque identifiant étranger détecté par `find_foreign_identifiers`."""
    result = text
    for identifier in find_foreign_identifiers(text, own_facts):
        result = result.replace(identifier, "••••")
    return result
