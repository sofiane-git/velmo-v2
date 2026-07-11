"""Étage 3b du pipeline garde-fous : Azure AI Content Safety — Prompt Shields
(G6, détection spécialisée injection/jailbreak). Renfort du LLM-juge sur G6,
jamais le seul filet — cf. conception_chantier2_guardrails.md.

Appelle directement l'API REST `text:shieldPrompt` (le SDK
`azure-ai-contentsafety` installé n'expose pas de méthode dédiée à Prompt
Shields, seulement `analyze_text`/`analyze_image` pour la modération G1/G2/G3).
"""

from __future__ import annotations

import os


def check(text: str) -> float | None:
    """Score d'injection 0..1, ou `None` si le service n'est pas configuré.

    `None` est ignoré par l'agrégation (`pipeline.py`) — Prompt Shields est un
    renfort de l'étage 1 (regex) et du LLM-juge sur G6, jamais le seul filet.
    """
    endpoint = os.getenv("AZURE_CONTENT_SAFETY_ENDPOINT")
    key = os.getenv("AZURE_CONTENT_SAFETY_KEY")
    if not endpoint or not key:
        return None

    import requests

    url = f"{endpoint.rstrip('/')}/contentsafety/text:shieldPrompt?api-version=2024-09-01"
    response = requests.post(
        url,
        headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json"},
        json={"userPrompt": text, "documents": []},
        timeout=5,
    )
    response.raise_for_status()
    data = response.json()
    attack_detected = bool(data.get("userPromptAnalysis", {}).get("attackDetected", False))
    return 1.0 if attack_detected else 0.0
