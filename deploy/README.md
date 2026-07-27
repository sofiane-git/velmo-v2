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

## Cible d'hébergement (prod) — Azure Container Apps

- **Hôte** : **Azure Container Apps** (ACA) — cette même image, `--target-port 8000`, ingress
  HTTPS externe, `--min-replicas 0` (scale-to-zero). Le code n'a aucune adhérence à l'hôte :
  config 12-factor, `GET /health` pour la sonde, `0.0.0.0:8000`.
- **Registre** : Azure Container Registry (image poussée via `az acr build`, tag = version du
  tag de release, ex. `velmo:v2.1.0` — traçable au commit taggé).
- **Secrets** : identité managée de l'app → Key Vault (`secretref`/`keyvaultref`), jamais de
  valeur en clair dans la config ACA ni dans l'image.
- **Store** : Azure Database for PostgreSQL Flexible Server (managé, PITR, `pgvector`).

Pas de manifeste IaC dédié ici : les commandes `az` de bout en bout sont dans
`docs/tutorials/tuto_azure_deploiement.md` §C/§D/§F (source unique), le *pourquoi* dans
`docs/reference/conceptions/conception_chantier3_evaluation_mlops.md` §Cible de déploiement.
Depuis `release.yml` (`approve-and-promote`), ce build+push+déploiement est **automatisé**
après approbation humaine (Environment `production`) — voir
`docs/tutorials/tuto_github_actions_release.md` §4bis/§7.4. Les commandes ci-dessus/§F
restent la référence pour un geste manuel (debug, test hors-release). Le front (`web/`) a son
propre cycle, sans registre conteneur — voir `docs/reference/cycle-de-vie-image.md`.

## Services requis (backing services, 12-factor)

| Service | Rôle | Pinning |
|---------|------|---------|
| PostgreSQL + pgvector | source de vérité (mémoire, audit, MLOps, checkpoints, embeddings, KB FAQ) | `pgvector/pgvector:pg18@sha256:…` |
| Ollama (Llama Guard 3) | classifieur de modération G1/G2/G3 | `ollama/ollama@sha256:…` |

En production, un Postgres configuré mais injoignable **fait échouer le
démarrage** (pas de repli SQLite silencieux) — voir `ALLOW_SQLITE_FALLBACK`
et `require_durable_store` (audit D3-03). Ollama reste en repli gracieux
(classifieur lexical) ; la KB FAQ (`velmo.kb_store`) aussi, vers `LocalKB`
(recherche lexicale) si Postgres est injoignable.

## Configuration

Toutes les variables via l'environnement (12-factor), cf. `.env.example`.
Aucun secret dans l'image (`.dockerignore` exclut `.env`) ni dans compose
(credentials Postgres interpolés depuis `.env`).

## Migrations

Chaîne Alembic linéaire, rejouable : `alembic upgrade head` (embarqué dans
l'image sous `/app/alembic`).

## Observabilité

Langfuse Cloud EU — voir [`docs/how-to/langfuse-setup.md`](../docs/how-to/langfuse-setup.md).
