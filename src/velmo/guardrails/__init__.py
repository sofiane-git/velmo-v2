"""Garde-fous d'entrée et de sortie de l'agent Velmo.

Surface publique stable consommée par l'agent et la suite d'acceptance.
Les méthodes de détection (modération, PII, injection, périmètre) sont à construire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Catégories de contenus contrôlés.
CATEGORIES = (
    "hate",
    "violence",
    "sexual",
    "pii",
    "out_of_scope",
    "prompt_injection",
    "secret_leak",
)


@dataclass
class Decision:
    """Verdict d'un garde-fou sur un message."""

    allowed: bool
    action: str  # "allow" | "block"
    category: str | None = None
    reason: str = ""
    refusal: str | None = None


@dataclass
class GuardrailEngine:
    """Applique les garde-fous d'entrée et de sortie et journalise les décisions."""

    events: list[dict[str, Any]] = field(default_factory=list)

    def check_input(self, message: str) -> Decision:
        """Contrôle un message entrant (modération, injection, périmètre)."""
        return Decision(allowed=True, action="allow")

    def check_output(self, text: str) -> Decision:
        """Contrôle une réponse sortante (PII, secrets, périmètre, modération)."""
        return Decision(allowed=True, action="allow")
