# Schéma de déploiement Azure de Velmo 2.0

## Ce que ce schéma raconte

C'est le schéma d'architecture globale (`00-architecture-globale.md`) **posé sur Azure** :
mêmes cinq étapes, même ordre, mais on voit maintenant **où** tourne l'app, **où** vivent les
secrets, et **où** sont lus/écrits les journaux. La chaîne
**garde-fou entrée → mémoire lecture → LLM → garde-fou sortie → mémoire écriture** est
préservée à l'intérieur de l'hôte.

```mermaid
flowchart TB
    B["🌐 Navigateur<br/>(origines : VELMO_WEB_ORIGINS)"]

    subgraph HOST["Azure Container Apps — ca-velmo-prod · identité managée · ingress HTTPS :8000"]
        direction TB
        GIN["🛡️ ① Garde-fou ENTRÉE<br/>regex/Luhn + Prompt Shields + Llama Guard + juge"]
        MR["🧠 ② Mémoire LECTURE<br/>faits user_id + épisodique pgvector"]
        LLM["🤖 ③ Azure AI Inference<br/>Mistral-Large-3"]
        GOUT["🛡️ ④ Garde-fou SORTIE<br/>PII redaction + juge"]
        MW["🧠 ⑤ Mémoire ÉCRITURE<br/>si confidence ≥ seuil (best-effort)"]
    end

    KV[("🔑 Key Vault<br/>clés IA · DB_URL · Langfuse")]
    PG[("🗄️ PostgreSQL Flexible Server<br/>faits + pgvector<br/>+ memory_audit + guardrail_audit")]
    OLL["Ollama / ACI<br/>Llama Guard 3 (privé)"]
    AISVC["Services IA cloud<br/>OpenAI juge · Language · Content Safety · Foundry"]
    LF[("📊 Langfuse Cloud EU<br/>traces (hors chemin de gate)")]

    B --> GIN --> MR --> LLM --> GOUT --> MW
    GOUT --> B
    MR -. lecture .-> PG
    MW -. écriture .-> PG
    GIN -. classif .-> OLL
    GIN -. injection/PII .-> AISVC
    GOUT -. PII .-> AISVC
    LLM -. appel .-> AISVC
    KV ==>|"secrets injectés au démarrage<br/>(secretref, via identité managée)"| HOST
    GIN -. journal blocage .-> PG
    MW -. journal écriture .-> PG
    HOST -. traces .-> LF

    classDef gate fill:#ffe0b2,stroke:#e65100,color:#4e2600;
    classDef mem fill:#c8e6c9,stroke:#2e7d32,color:#1b5e20;
    classDef flow fill:#e3f2fd,stroke:#1565c0,color:#0d47a1;
    classDef store fill:#ede7f6,stroke:#4527a0,color:#311b92;
    class GIN,GOUT gate;
    class MR,MW mem;
    class B,LLM flow;
    class KV,PG,OLL,AISVC,LF store;
```

## Où passent les secrets

- **Key Vault** est la source de vérité unique. L'app (ACA) porte une **identité managée** à
  qui on donne le rôle *Key Vault Secrets User* (tuto §D3).
- Chaque **clé** et la **chaîne de connexion** (`DB_URL`, avec mot de passe) sont déclarées
  comme des **références** Key Vault (`secretref` → `keyvaultref`) et injectées en variables
  d'environnement **au démarrage de la révision** (flèche épaisse). Aucune valeur en clair dans
  la config ACA, l'image, ou Git.
- Les **endpoints** (URL publiques, non secrètes) restent des variables d'app ordinaires.

## Où sont lus/écrits les journaux

- **Décisions des garde-fous** → table `guardrail_audit` (Postgres) : log de sécurité, conservé
  même après une demande d'oubli (intérêt légitime, cf. conception Ch.2).
- **Écritures mémoire** → table `memory_audit` (Postgres) : soumis au droit à l'oubli (R5).
- **Traces d'observabilité** (drill-down par composant) → **Langfuse Cloud EU**, hors du chemin
  de gate ; `eval_run` ne stocke qu'un pointeur (URL de trace), pas la donnée.
- **Logs d'infrastructure** (stdout des conteneurs) → Log Analytics de l'environnement ACA.

## Correspondance avec l'architecture globale

| Étape (schéma 00) | Service Azure |
| --- | --- |
| ① Garde-fou entrée | ACA (regex local) + Ollama/ACI (Llama Guard) + Azure Content Safety + juge Azure OpenAI |
| ② Mémoire lecture | PostgreSQL Flexible Server (+ pgvector) |
| ③ Agent / LLM | Azure AI Inference (Mistral-Large-3) |
| ④ Garde-fou sortie | Azure AI Language (PII) + juge Azure OpenAI |
| ⑤ Mémoire écriture | PostgreSQL Flexible Server |

Le *pourquoi* des choix (ACA vs App Service, Postgres managé pour R2/R3) :
`../conceptions/conception_chantier3_evaluation_mlops.md` §Cible de déploiement.
Le *comment* (commandes `az`) : `../tuto_azure_deploiement.md` §C/§D/§F.
