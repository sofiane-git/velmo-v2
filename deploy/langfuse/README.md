# Langfuse self-host — Velmo 2.0

## Local/dev

    git clone --branch v3 https://github.com/langfuse/langfuse.git ../../.vendor/langfuse
    cp .env ../../.vendor/langfuse/.env
    # Compléter .env avec les secrets internes (voir .env.example, noms exacts
    # à relever dans le docker-compose.yml cloné — ils évoluent avec les releases).
    cd ../../.vendor/langfuse && docker compose up -d

Vérifier : `curl -f http://localhost:3000/api/public/health` (endpoint santé officiel,
`self-hosting/configuration/health-readiness-endpoints`). Une fois up, brancher
`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_BASE_URL=http://localhost:3000` (les
valeurs `LANGFUSE_INIT_PROJECT_PUBLIC_KEY`/`_SECRET_KEY` du `.env`) dans l'environnement local
avant `uv run python -m velmo.mlops.score`.

## Production

Docker Compose est **déconseillé en prod par Langfuse lui-même** (pas de haute dispo, pas de
sauvegarde). Utiliser le module Terraform officiel Azure :
https://github.com/langfuse/langfuse-terraform-azure — voir
`docs/job/tuto_azure_deploiement.md` §10 pour la procédure complète dans le contexte de ce
projet (région UE obligatoire — conception §Observabilité, RGPD).
