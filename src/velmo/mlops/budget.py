"""Allocation du budget de latence par composant — Ch.0 §6, audit O-04.

Le gate contrôle un **total** (`p95 ≤ 4000 ms`). La conception disait la latence
« décomposée par composant » sans dire *combien* chaque composant avait droit de
consommer : un dépassement ne désignait donc aucun coupable, et les seuils locaux
cités ailleurs (les 800 ms du classifieur au Ch.2) étaient des chiffres orphelins
que rien ne rattachait au budget qu'ils étaient censés fractionner.

Ce module est la table d'arbitrage. C'est **elle** qu'il faut rouvrir pour
changer un seuil local, pas la section du chantier qui le cite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

# Allocation p95 par composant instrumenté (millisecondes). Les noms sont ceux
# passés à `ObservabilitySink.on_llm_call(component=...)` par les enveloppes
# d'instrumentation (`observability.Instrumented*`).
#
# Les deux portes de garde-fous exécutent leurs étages 2 et 3 **en parallèle** :
# le coût d'une porte est `max(classifieur, juge)`, pas leur somme. Les
# allocations ci-dessous sont donc des plafonds par appel, et la somme de la
# table garde une marge sous le plafond total (vérifié par un test).
LATENCY_ALLOCATION_MS: dict[str, float] = {
    # Poste dominant : génération de la réponse complète.
    "agent": 1800.0,
    # Classifieur de modération, backend cloud (défaut). Le backend local
    # Llama Guard 3 8B a une allocation plus large (800 ms) documentée au Ch.2,
    # appliquée quand c'est lui qui répond.
    "guardrails_classifier": 800.0,
    # Juge contextuel, sur le chemin bloquant, deux appels par tour
    # (entrée + sortie) — allocation par appel.
    "guardrails_judge": 900.0,
    # Résumé glissant (R4) : ne tourne qu'au franchissement du budget de tokens.
    "memory_summary": 400.0,
    # Extracteur : **asynchrone après la réponse**, donc hors du budget du tour.
    # Borné quand même, pour qu'une file d'extraction ne s'accumule pas.
    "memory_extractor": 3000.0,
}

# Composants hors budget du tour (mesurés et publiés, mais leur dépassement ne
# concerne pas la latence perçue par le client).
ASYNC_COMPONENTS: frozenset[str] = frozenset({"memory_extractor"})

# Postes non-LLM du budget du tour (pas d'appel `on_llm_call`, donc absents de la
# table ci-dessus, mais bien présents dans le budget du Ch.0 §6).
REGEX_STAGE_MS = 40.0  # étage 1, entrée + sortie
MEMORY_AND_TOOLS_MS = 250.0  # lecture mémoire, outils, KB (requêtes locales)
NETWORK_MARGIN_MS = 110.0


def synchronous_turn_p95_ms() -> float:
    """Budget p95 d'un tour complet, composé comme au Ch.0 §6.

    Ce **n'est pas** la somme de `LATENCY_ALLOCATION_MS` : les étages 2 et 3 de
    chaque porte tournent **en parallèle**, donc une porte coûte
    `max(classifieur, juge)` et non leur somme ; il y a **deux** portes (entrée et
    sortie) ; et l'extracteur est asynchrone, donc hors budget. Une somme naïve
    surestimerait le tour de plus de 70 %.
    """
    gate_ms = max(
        LATENCY_ALLOCATION_MS["guardrails_classifier"],
        LATENCY_ALLOCATION_MS["guardrails_judge"],
    )
    return (
        REGEX_STAGE_MS
        + 2 * gate_ms
        + LATENCY_ALLOCATION_MS["agent"]
        + MEMORY_AND_TOOLS_MS
        + NETWORK_MARGIN_MS
    )


@dataclass(frozen=True)
class ComponentLatency:
    """Latence observée d'un composant, face à son allocation."""

    component: str
    p95_ms: float
    allocation_ms: float | None
    over_budget: bool
    calls: int
    is_async: bool

    def to_dict(self) -> dict[str, object]:
        """Forme JSON-sérialisable — le payload des événements de gate est
        diffusé en SSE et ne doit contenir aucun objet métier (contrat
        `GateEvent`)."""
        return {
            "component": self.component,
            "p95_ms": self.p95_ms,
            "allocation_ms": self.allocation_ms,
            "over_budget": self.over_budget,
            "calls": self.calls,
            "is_async": self.is_async,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> ComponentLatency:
        allocation = raw["allocation_ms"]
        return cls(
            component=str(raw["component"]),
            p95_ms=cast(float, raw["p95_ms"]),
            allocation_ms=None if allocation is None else cast(float, allocation),
            over_budget=bool(raw["over_budget"]),
            calls=cast(int, raw["calls"]),
            is_async=bool(raw["is_async"]),
        )


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(int(q * len(sorted_values)), len(sorted_values) - 1)
    return sorted_values[index]


def component_latency_report(
    latencies_by_component: dict[str, list[float]],
) -> list[ComponentLatency]:
    """Une ligne par composant observé, avec son p95 et son verdict.

    Un composant **sans allocation** est publié mais n'est jamais déclaré hors
    budget : le déclarer en dépassement face à un plafond qui n'existe pas serait
    une fausse alerte. C'est un signal qu'il faut l'ajouter à la table, pas un
    échec du composant.
    """
    rows: list[ComponentLatency] = []
    for component, values in sorted(latencies_by_component.items()):
        allocation = LATENCY_ALLOCATION_MS.get(component)
        p95 = _percentile(sorted(values), 0.95)
        rows.append(
            ComponentLatency(
                component=component,
                p95_ms=p95,
                allocation_ms=allocation,
                over_budget=allocation is not None and p95 > allocation,
                calls=len(values),
                is_async=component in ASYNC_COMPONENTS,
            )
        )
    return rows
