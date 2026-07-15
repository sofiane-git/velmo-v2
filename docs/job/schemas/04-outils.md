# Les outils de Velmo 2.0

## Ce que ce schéma raconte

Chaque brique de la stack a un rôle précis et ne fait qu'une chose — pas d'outil qui recouvre le travail d'un autre. Ce schéma regroupe tous les outils vus dans les trois chantiers, classés par étape : ce avec quoi l'agent tourne, ce qui le surveille, ce qui le teste, ce qui le déploie.

```mermaid
flowchart TB
    subgraph MAIN[" "]
        subgraph AGENT["🤖⚙️ En fonctionnement (agent + garde-fous)"]
            LC["LangChain<br/>orchestration + abstractions mémoire<br/>(historique, résumé glissant, retriever)"]
            PG[("PostgreSQL<br/>mémoire court/long terme, isolation par user_id,<br/>audit garde-fous, versions & verdicts CI")]
            CH[("ChromaDB<br/>embeddings des épisodes mémoire<br/>+ recherche par similarité")]
            LGM["Llama Guard 3 8B (Ollama, local)<br/>+ repli lexical FR<br/>classifieur haine/violence/sexuel"]
            AZ["Azure OpenAI<br/>LLM-juge (garde-fous sortie,<br/>anti-injection, hors périmètre)"]
        end

        subgraph EVAL["🧪 Évaluation (à chaque PR)"]
            JSONL["Fixtures JSONL<br/>memory/guardrail/quality_cases<br/>versionnées comme du code"]
            DE["DeepEval<br/>métriques qualité (G-Eval, faithfulness,<br/>rétention conversationnelle) — pytest local"]
        end

        subgraph CI["⚙️ CI/CD"]
            GHA["GitHub Actions<br/>quality.yml : 3 suites + gate par dimension"]
            GH["GitHub<br/>GitFlow simplifié : develop + main protégée + hotfix/*<br/>Environments staging (auto depuis develop)/production"]
        end

        subgraph OBS["📡 Observabilité"]
            LF[("Langfuse<br/>trace chaque appel LLM : coût, latence<br/>Prompt Management (staging/production)<br/>Dataset Runs")]
        end

        JSONL --> DE --> GHA
        GHA --> GH
        LC -.->|"appels tracés"| LF
        LGM -.->|"appels tracés"| LF
        AZ -.->|"appels tracés"| LF
        DE -.->|"Dataset Run"| LF
        GHA -->|"INSERT agent_version / eval_run<br/>(gate_passed)"| PG
    end

    MAIN --- LG1

    subgraph LEGEND["Légende"]
        direction LR
        LG1["En fonctionnement<br/>(agent + garde-fous)"]
        LG2["Évaluation"]
        LG3["CI/CD"]
        LG4["Observabilité"]
    end

    classDef agent fill:#e3f2fd,stroke:#1565c0,color:#0d47a1;
    classDef eval fill:#fff9c4,stroke:#f9a825,color:#5c4400;
    classDef ci fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20;
    classDef obs fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c;
    class LC,PG,CH,LGM,AZ,LG1 agent;
    class JSONL,DE,LG2 eval;
    class GHA,GH,LG3 ci;
    class LF,LG4 obs;
    style MAIN fill:none,stroke:none;
    %% connecteur invisible (force la légende en bas) — index = ordre des flèches, recalculer si le flux change
    linkStyle 8 stroke:none;
```

## Les points traités dans ce document

- **Un outil, une responsabilité** : PostgreSQL décide (verdicts, isolation, pass/fail) ; Langfuse observe (coût, latence, détail de chaque appel) ; ChromaDB retrouve par similarité (embeddings) ; aucun ne duplique le travail d'un autre — le détail de chaque choix est justifié dans le chantier correspondant.
- **Trois natures de détection dans les garde-fous, trois outils différents** : regex/motifs (déterministe, gratuit, pour PII/formats connus), Llama Guard 3 8B via Ollama + repli lexical FR (classifieur local, rapide, pour haine/violence/sexuel explicite), Azure OpenAI en LLM-juge (coûteux mais contextuel, pour l'injection de prompt et le hors-périmètre) — chacun couvre l'angle mort de l'autre (détail : [`conception_chantier2_guardrails.md`](../conceptions/conception_chantier2_guardrails.md)).
- **DeepEval en local, pas de compte externe** : les résultats restent dans `EVAL_RUN` (Postgres) et `mlops/report.md` — pas de dépendance à un service tiers pour un gate qui bloque la livraison.
- **LangChain comme colonne vertébrale mémoire** : `PostgresChatMessageHistory` (court terme), `ConversationSummaryBufferMemory` (résumé glissant R4), retriever `Chroma` (épisodes) — évite de réécrire à la main la gestion de fenêtre de contexte.
- **GitHub à trois niveaux** : le dépôt (branches `develop`/`main`/`hotfix/*`, PR, revue) *et* Actions (exécution CI, paliers différenciés par déclencheur) *et* Environments (`staging` redéployé automatiquement à chaque merge dans `develop` ; `production` en retag automatique au merge `develop → main`, sans rebuild) — trois usages du même outil, pas trois outils.
- **Le LLM principal de l'agent n'est pas figé ici** : le chantier 1 (mémoire) laisse le choix du modèle ouvert délibérément — seul le pipeline d'orchestration (LangChain) et les composants de contrôle (Llama Guard 3, Azure OpenAI en LLM-juge) sont arrêtés dans la conception.
