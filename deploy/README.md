# Déploiement — Velmo 2.0

Référence du **workload déployé**, pour rendre vérifiable la parité dev ↔ prod
(audit D6-09). `docker-compose.yml` (racine) est le miroir de développement de
ce contrat, pas une configuration divergente.

## Unité déployée

- **Image** : `velmo-v2` (voir `Dockerfile`, multi-stage, runtime non-root).
- **Commande** : `uvicorn velmo.api:app --host 0.0.0.0 --port 8000`
  — c'est le `CMD` de l'image **et** ce que lance le service `app` de compose
  (aucun override). La CLI (`python -m velmo.cli`) reste accessible via
  `docker compose exec app python -m velmo.cli`, mais n'est pas le workload servi.
- **Port** : 8000 (HTTP, endpoint de santé `GET /health`).

## Services requis (backing services, 12-factor)

| Service | Rôle | Pinning |
|---------|------|---------|
| PostgreSQL + pgvector | source de vérité (mémoire, audit, MLOps, checkpoints, embeddings) | `pgvector/pgvector:pg16@sha256:…` |
| Ollama (Llama Guard 3) | classifieur de modération G1/G2/G3 | `ollama/ollama@sha256:…` |
| Chroma | KB vectorielle FAQ | `chromadb/chroma@sha256:…` |

En production, un Postgres configuré mais injoignable **fait échouer le
démarrage** (pas de repli SQLite silencieux) — voir `ALLOW_SQLITE_FALLBACK`
et `require_durable_store` (audit D3-03). Ollama et Chroma restent en repli
gracieux (classifieur lexical, KB locale).

## Configuration

Toutes les variables via l'environnement (12-factor), cf. `.env.example`.
Aucun secret dans l'image (`.dockerignore` exclut `.env`) ni dans compose
(credentials Postgres interpolés depuis `.env`).

## Migrations

Chaîne Alembic linéaire, rejouable : `alembic upgrade head` (embarqué dans
l'image sous `/app/alembic`).

## Observabilité

Langfuse Cloud EU — voir [`langfuse/README.md`](langfuse/README.md).
