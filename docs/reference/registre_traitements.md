# Registre des traitements — Velmo 2.0 (RGPD art. 30)

Registre consolidé des traitements de données personnelles opérés par l'agent
de support Velmo 2.0. Référencé par `conception_chantier1_memoire.md`
(§Rétention, §Base légale) et `conception_chantier3_evaluation_mlops.md`
(§Observabilité/Gouvernance).

**Responsable de traitement :** Velmo (boutique de maillots collector).
**Contexte :** projet pédagogique — pas de vraies données client en production ;
les mesures ci-dessous décrivent la posture cible si le projet traitait des
données réelles.

## Traitements

| # | Traitement | Finalité | Base légale (art. 6) | Catégories de données | Conservation | Localisation / sous-traitant |
|---|-----------|----------|----------------------|-----------------------|--------------|------------------------------|
| T1 | Mémoire long terme (faits, règles) | Personnaliser le support (contexte client dans le temps, R1–R2) | **art. 6(1)(b)** exécution du contrat de support | Préférences, faits déclarés par le client (taille, club, historique de commande) | Jusqu'à demande d'effacement (R5) ; pas de TTL calendaire | PostgreSQL, région **UE** |
| T2 | Mémoire épisodique (embeddings) | Retrouver les échanges similaires (R1) | art. 6(1)(b) | Texte des tours de conversation + embeddings | Idem T1 (effacé avec T1, R5 atomique via pgvector) | PostgreSQL + pgvector, **UE** |
| T3 | État conversationnel (checkpoints LangGraph) | Reprise de session (R2) | art. 6(1)(b) | Messages + résumé de thread | Effacé par `forget_all` (R5, purge checkpoints incluse) | PostgreSQL, **UE** |
| T4 | Journal garde-fous (`guardrail_audit`) | Sécurité, anti-abus, détection d'attaque répétée | **art. 6(1)(f)** intérêt légitime (sécurité du service) | Catégorie de risque, horodatage, `user_id`, méthode | **Survit** à l'effacement R5 (intérêt légitime : investigation d'incident) — à anonymiser plutôt que détruire | PostgreSQL, **UE** |
| T5 | Observabilité qualité (Langfuse) | Mesure de qualité MLOps (hors chemin de gate) | art. 6(1)(f) intérêt légitime | Traces d'appels LLM **après redaction PII** | Selon rétention Langfuse Cloud EU | **Langfuse Cloud, région EU** (sous-traitant art. 28) |

## Sous-traitants (art. 28)

| Sous-traitant | Rôle | Localisation |
|---------------|------|--------------|
| Microsoft Azure (AI Inference / OpenAI / AI Language) | LLM agent, juge garde-fous, PII redaction | Déploiements **région UE** |
| Azure AI Foundry (Anthropic `claude-opus-4-5`) | Extracteur mémoire + juge DeepEval | Région **UE** |
| Langfuse Cloud | Observabilité (traces qualité, PII redactée en amont) | **EU** (`cloud.langfuse.com`, pas `us.`) |
| Ollama (Llama Guard 3) | Classifieur de modération | **Auto-hébergé / local** — aucun transfert |

## Droits des personnes

| Droit | Article | État |
|-------|---------|------|
| Accès / inspection de ce qui est mémorisé | art. 15 | Journal des écritures mémoire (`memory_audit`) — traçabilité R6 |
| **Effacement (droit à l'oubli)** | art. 17 | ✅ `MemoryManager.forget_all` : purge faits/règles/épisodes/checkpoints + **tombstone** anti-résurrection (R5) |
| Limitation de conservation | art. 5 | Effacement à la demande ; `guardrail_audit` conservé au titre de l'intérêt légitime sécurité (à anonymiser) |
| **Portabilité** (export) | art. 20 | ⚠️ **Non implémenté à ce jour** (aucun `export_user`) — gap connu (audit D9-09), à exposer côté API mémoire |
| PII hors traces tierces | art. 5/25 | Redaction avant export Langfuse (T5) |

## Minimisation (art. 5)

Extraction mémoire cadrée (seuil de confiance `memory_confidence_threshold`,
`memory/extractor.py`) : seuls les faits utiles au support sont retenus, pas le
verbatim brut. PII structurée masquée avant persistance (`guardrails.redact_pii`).
