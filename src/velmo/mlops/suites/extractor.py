"""Suite extracteur — précision d'écriture mémoire (audit O-01).

`eval/memory_confidence_cases.jsonl` existait depuis le Ch.1 (§Calibration du
seuil de confiance) : 27 échanges labellisés « à retenir / à jeter », avec un
propriétaire nommé. Mais aucune suite ne le rejouait, aucun champ de rapport ne
l'exposait, et sa seule occurrence dans le code était un **commentaire** de
configuration. Le composant qui décide ce qui entre en mémoire durable — donc ce
qui sera réinjecté à vie et visé par le droit à l'oubli — était le seul du
système sans métrique.

**Hors gate, volontairement.** Ce que mesure cette suite est un jugement sur des
cas limites (« je crois que je faisais du 42 avant, plus trop sûr ») : c'est
bruité par nature. La règle du Ch.3 s'applique — ce qui est exactement
vérifiable gate, ce qui relève du jugement reste en reporting. La suite sert à
calibrer `MEMORY_CONFIDENCE_THRESHOLD`, pas à bloquer une livraison.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from velmo.memory.extractor import FactExtractor, get_extractor

FIXTURE = Path("eval/memory_confidence_cases.jsonl")


@dataclass(frozen=True)
class ExtractorSuiteResult:
    """Matrice de confusion de la décision de rétention (positif = « retenir »)."""

    total: int
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    # Toujours `False` : la suite est en reporting. Champ explicite plutôt
    # qu'implicite, pour qu'un futur passage au gate soit une décision visible.
    gates: bool = False

    @property
    def precision(self) -> float:
        """Parmi ce que l'extracteur a retenu, quelle part devait l'être.

        C'est la métrique la plus importante des deux : un faux positif écrit une
        donnée durable erronée (pollution de la mémoire, et une donnée
        personnelle conservée sans raison).
        """
        retained = self.true_positives + self.false_positives
        return self.true_positives / retained if retained else 0.0

    @property
    def recall(self) -> float:
        """Parmi ce qui devait être retenu, quelle part l'a été. Un faux négatif
        est moins grave : l'information peut être redonnée par le client."""
        expected = self.true_positives + self.false_negatives
        return self.true_positives / expected if expected else 0.0


def _load_cases(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def run_extractor_suite(
    extractor: FactExtractor | None = None,
    *,
    fixture: Path = FIXTURE,
    threshold: float | None = None,
) -> ExtractorSuiteResult:
    """Rejoue le jeu labellisé et renvoie la matrice de confusion.

    Un cas est compté « retenu » si l'extracteur produit au moins un fait ou une
    règle dont la confiance atteint le seuil — c'est exactement la décision que
    prend `MemoryManager.write`, pas une approximation de celle-ci.
    """
    from velmo.config import get_settings

    extractor = extractor or get_extractor()
    if threshold is None:
        threshold = get_settings().memory_confidence_threshold

    tp = fp = fn = tn = 0
    for case in _load_cases(fixture):
        result = extractor.extract(case["message"], "")
        # Faits et règles ont chacun leur type : on collecte les confiances
        # séparément plutôt que de fusionner les objets (la liste mixte
        # s'effondrerait sur leur base commune, qui n'expose pas `confidence`).
        confidences = [fact.confidence for fact in result.facts]
        confidences += [procedure.confidence for procedure in result.procedures]
        retained = any(confidence >= threshold for confidence in confidences)
        should_retain = case["decision"] == "retain"
        if retained and should_retain:
            tp += 1
        elif retained and not should_retain:
            fp += 1
        elif not retained and should_retain:
            fn += 1
        else:
            tn += 1

    return ExtractorSuiteResult(
        total=tp + fp + fn + tn,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        true_negatives=tn,
    )
