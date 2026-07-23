# Multi-stage à 3 cibles :
#   `dev`     — source + node_modules complets, hot-reload (docker-compose, target: dev)
#   `runtime` — sortie Nitro seule (`.output/`), serveur Node autonome — cible
#               par défaut d'un `docker build .` simple (déploiement)
FROM node:22-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS base

RUN corepack enable

WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .


FROM base AS dev

EXPOSE 3000
CMD ["pnpm", "dev", "--host", "0.0.0.0"]


FROM base AS build

RUN pnpm build


FROM node:22-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS runtime

WORKDIR /app

# Conteneur en utilisateur non privilégié — jamais root au runtime (même
# convention que le Dockerfile backend, D6-02).
RUN useradd --create-home --uid 10001 app

# Sortie Nitro (preset par défaut node-server) : serveur autonome, aucune
# dépendance à pnpm/node_modules de dev.
COPY --from=build --chown=app:app /app/.output ./.output

USER app

ENV HOST=0.0.0.0 \
    PORT=3000

EXPOSE 3000

CMD ["node", ".output/server/index.mjs"]
