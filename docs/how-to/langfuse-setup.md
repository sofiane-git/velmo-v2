# Langfuse Cloud — Velmo 2.0

Projet pédagogique (pas de vraies conversations client en prod) → Langfuse Cloud plutôt que
self-host, pour un setup en quelques minutes. Self-host resterait la bonne pratique si ce
projet traitait un jour de vraies données client (voir
`docs/reference/conceptions/conception_chantier3_evaluation_mlops.md` §Gouvernance RGPD).

## Setup

1. Créer un compte sur https://cloud.langfuse.com (région **EU** — pas `us.cloud.langfuse.com`).
2. Créer un projet (ex. `velmo-mlops`).
3. **Settings** → **API Keys** → **Create new API key** → récupérer `Public Key` (`pk-lf-...`)
   et `Secret Key` (`sk-lf-...`).
4. Renseigner dans `.env` (voir `.env.example`) :

       LANGFUSE_PUBLIC_KEY=pk-lf-...
       LANGFUSE_SECRET_KEY=sk-lf-...
       LANGFUSE_BASE_URL=https://cloud.langfuse.com

Vérifier : lancer un run (`uv run python -m velmo.mlops.score`), puis contrôler que la trace
apparaît dans le dashboard du projet Langfuse Cloud.

Les 3 variables absentes → `get_sink()` (`src/velmo/mlops/observability.py`) retombe sur
`NullSink`, aucun impact sur le gate (Langfuse reste hors chemin de gate — voir conception
§Observabilité).
