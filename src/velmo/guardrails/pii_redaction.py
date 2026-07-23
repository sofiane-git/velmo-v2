"""Étage 3c du pipeline garde-fous, sortie uniquement : Azure AI Language —
PII redaction en texte libre (G4). Complète `patterns.py` (regex/Luhn,
formats structurés) sur l'angle mort documenté : noms, adresses, e-mails
d'un autre client en texte libre — cf. conception_chantier2_guardrails.md.

Asymétrie entrée/sortie assumée (D4-04) : le risque G4 en texte libre est la
fuite **inter-clients** — une donnée d'un *autre* client qui remonterait dans
la réponse de l'agent — donc un risque de **sortie**. En entrée, la PII en
texte libre est la donnée propre de l'utilisateur (pas une fuite) ; la PII
**structurée** (carte, IBAN, mot de passe) y est déjà bloquée par l'étage 1
(`patterns.scan_pii`), et le contrôle inter-clients le plus précieux est le
cross-check `user_id` en sortie (conception §G4 en sortie). Étendre la
redaction cloud à l'entrée n'ajouterait donc aucune protection, seulement du
coût et des faux positifs sur des noms légitimes.
"""

from __future__ import annotations

from velmo.config import Settings, get_settings


def scan(text: str, settings: Settings | None = None) -> list[tuple[int, int]] | None:
    """Spans PII détectés (`(offset, length)`) ; `[]` si aucun ; `None` si le
    service n'est **pas configuré**.

    Ces trois cas sont distingués (au lieu d'un `[]` fourre-tout) pour que
    `pipeline.py` puisse appliquer la matrice de repli : une **panne** du
    service configuré (erreur Azure, réseau) lève — le repli fail-open G4
    s'applique alors ; une simple **non-configuration** (`None`) n'est pas une
    panne et ne déclenche aucun repli (feature-flag désactivé = « n'ajoute
    rien », conception_chantier2_guardrails.md §Repli).

    `settings` : réutilisé depuis `pipeline.run()` si fourni, sinon résolu ici.
    """
    settings = settings or get_settings()
    endpoint = settings.azure_language_endpoint
    key = settings.azure_language_key
    if not endpoint or not key:
        return None

    from azure.ai.textanalytics import TextAnalyticsClient
    from azure.core.credentials import AzureKeyCredential

    client = TextAnalyticsClient(endpoint, AzureKeyCredential(key))
    result = client.recognize_pii_entities([text])[0]
    if result.is_error:
        # Erreur du service configuré : ne pas la déguiser en « aucune PII »
        # (fuite silencieuse) — remonter pour que le repli G4 s'applique.
        raise RuntimeError(f"Azure AI Language a échoué : {result.error}")
    return [(e.offset, e.length) for e in result.entities]


def redact_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """Masque les segments `(offset, length)` par `[PII masquée]`, appliqués de
    droite à gauche pour que le masquage d'un span ne décale pas les offsets
    (calculés sur le texte d'origine) des spans encore à traiter."""
    for offset, length in sorted(spans, key=lambda s: s[0], reverse=True):
        text = text[:offset] + "[PII masquée]" + text[offset + length :]
    return text
