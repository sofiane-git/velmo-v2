"""Rapport de suivi (`mlops/report.md` + sidecar `.json`) — contrat fixe,
lisible par un humain et parsable par la CI/un dashboard. Voir
conception_chantier3_evaluation_mlops.md §Rapport de suivi.
"""

from __future__ import annotations

import json
from pathlib import Path

from velmo.mlops import Scores


def write_report(scores: Scores, path: Path) -> None:
    markdown = f"""# Rapport d'évaluation Velmo 2.0

## Notes

- Note memoire : {scores.memory:.2%}
- Note garde-fous : {scores.guardrails:.2%}
- Note qualite : {scores.quality:.2%}
- Note globale (gate) : {scores.global_:.2%}

## Garde-fous

- Taux de blocage (rappel) : {scores.block_rate:.2%}
- Taux de faux positifs : {scores.false_positive_rate:.2%}

## Performance

- Latence p95 : {scores.latency_ms:.0f} ms
- Cout par conversation : {scores.cost:.4f} €
"""
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
    }
    sidecar_path = path.with_suffix(".json")
    sidecar_path.write_text(json.dumps(sidecar_data, indent=2, ensure_ascii=False), encoding="utf-8")
