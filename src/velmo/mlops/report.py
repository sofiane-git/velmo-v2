"""Rapport de suivi (`mlops/report.md` + sidecar `.json`) — contrat fixe,
lisible par un humain et parsable par la CI/un dashboard. Voir
conception_chantier3_evaluation_mlops.md §Rapport de suivi.

**Règle de contrat (audit O-01..O-04).** Toute obligation de mesure inscrite dans
un chantier nomme ici le champ qui la porte. Trois mesures étaient auparavant
déclarées ailleurs et jamais publiées : la précision de l'extracteur (jeu de cas
présent, jamais rejoué), la divergence du juge shadow (colonne écrite, jamais
relue) et la latence par composant (gatée en total, jamais décomposée). Une
mesure sans lecteur ne calibre rien — c'est un coût sans bénéfice, et pire, une
illusion de contrôle.
"""

from __future__ import annotations

import json
from pathlib import Path

from velmo.mlops import Scores

# Une mesure absente s'affiche comme telle, jamais comme un zéro : un « 0,0 % »
# de divergence shadow se lirait comme un repli parfaitement calibré là où la
# vérité est « repli jamais exercé sur ce run ».
_UNMEASURED = "non mesuré"


def _pct(value: float | None) -> str:
    return f"{value:.2%}" if value is not None else _UNMEASURED


def _component_lines(scores: Scores) -> str:
    if not scores.latency_by_component:
        return f"- Décomposition par composant : {_UNMEASURED}\n"
    lines = []
    for row in scores.latency_by_component:
        if row.allocation_ms is None:
            verdict = "pas d'allocation définie"
        elif row.over_budget:
            verdict = f"DÉPASSEMENT (alloué {row.allocation_ms:.0f} ms)"
        else:
            verdict = f"dans le budget (alloué {row.allocation_ms:.0f} ms)"
        suffix = " [asynchrone, hors budget du tour]" if row.is_async else ""
        lines.append(
            f"- {row.component} : p95 {row.p95_ms:.0f} ms sur {row.calls} appel(s) "
            f"— {verdict}{suffix}\n"
        )
    return "".join(lines)


def write_report(scores: Scores, path: Path) -> None:
    shadow = _pct(scores.judge_shadow_divergence_rate)
    markdown = f"""# Rapport d'évaluation Velmo 2.0

## Notes

- Note memoire : {scores.memory:.2%}
- Note garde-fous : {scores.guardrails:.2%}
- Note qualite : {scores.quality:.2%}
- Note globale (gate) : {scores.global_:.2%}

## Garde-fous

- Taux de blocage (rappel) : {scores.block_rate:.2%}
- Taux de faux positifs : {scores.false_positive_rate:.2%}
- Divergence du juge de repli (shadow, seuil d'attention 20%) : {shadow}

## Outils — actions metier

- Note outils (cas deterministes : refus + confirmation) : {_pct(scores.tools)}
- Justesse de selection d'outil (reporting) : {_pct(scores.tool_selection_accuracy)}

## Memoire — extracteur (reporting, hors gate)

- Precision d'ecriture : {_pct(scores.extractor_precision)}
- Rappel d'ecriture : {_pct(scores.extractor_recall)}

## Performance

- Latence p95 (total) : {scores.latency_ms:.0f} ms
- Cout par conversation : {scores.cost:.4f} €

### Latence par composant

{_component_lines(scores)}"""
    path.write_text(markdown, encoding="utf-8")

    sidecar_data = {
        "note_memoire": scores.memory,
        "note_garde_fous": scores.guardrails,
        "note_qualite": scores.quality,
        "note_globale": scores.global_,
        "taux_blocage": scores.block_rate,
        "taux_faux_positifs": scores.false_positive_rate,
        "latence_p95_ms": scores.latency_ms,
        "cout_par_conversation": scores.cost,
        "taux_divergence_shadow": scores.judge_shadow_divergence_rate,
        "extracteur_precision": scores.extractor_precision,
        "extracteur_rappel": scores.extractor_recall,
        "note_outils": scores.tools,
        "justesse_selection_outil": scores.tool_selection_accuracy,
        "latence_par_composant": [
            {
                "composant": row.component,
                "p95_ms": row.p95_ms,
                "allocation_ms": row.allocation_ms,
                "hors_budget": row.over_budget,
                "appels": row.calls,
                "asynchrone": row.is_async,
            }
            for row in scores.latency_by_component
        ],
    }
    sidecar_path = path.with_suffix(".json")
    sidecar_json = json.dumps(sidecar_data, indent=2, ensure_ascii=False)
    sidecar_path.write_text(sidecar_json, encoding="utf-8")
