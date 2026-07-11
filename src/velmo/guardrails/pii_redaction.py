"""Étage 3c du pipeline garde-fous, sortie uniquement : Azure AI Language —
PII redaction en texte libre (G4). Complète `patterns.py` (regex/Luhn,
formats structurés) sur l'angle mort documenté : noms, adresses, e-mails
d'un autre client en texte libre — cf. conception_chantier2_guardrails.md.
"""

from __future__ import annotations

import os


def scan(text: str) -> list[tuple[int, int]]:
    """Liste des spans PII détectés (`(offset, length)`), vide si non configuré."""
    endpoint = os.getenv("AZURE_LANGUAGE_ENDPOINT")
    key = os.getenv("AZURE_LANGUAGE_KEY")
    if not endpoint or not key:
        return []

    from azure.ai.textanalytics import TextAnalyticsClient
    from azure.core.credentials import AzureKeyCredential

    client = TextAnalyticsClient(endpoint, AzureKeyCredential(key))
    result = client.recognize_pii_entities([text])[0]
    if result.is_error:
        return []
    return [(e.offset, e.length) for e in result.entities]
