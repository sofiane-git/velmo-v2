# Multi-stage (D6-06) : l'étage `builder` porte uv + le lockfile + l'outillage
# de résolution ; l'image finale `runtime` ne garde que l'interpréteur, le venv
# résolu et les sources — pas uv, pas de cache de build.
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# uv pinné par tag précis (D6-03) — pas `:latest`, pour un outillage de build
# reproductible.
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
COPY src ./src
COPY eval ./eval
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts
COPY kb ./kb

# --locked : échoue si `uv.lock` diverge de `pyproject.toml` (build déterministe,
# supply-chain B3/D6-07) au lieu de re-résoudre silencieusement.
RUN uv sync --locked --no-dev --extra vector --extra llm --extra guardrails --extra graph


FROM python:3.11-slim AS runtime

# Le venv résolu est sur le PATH : l'image tourne sans uv (retiré avec l'étage
# builder).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Conteneur en utilisateur non privilégié (D6-02) : jamais root au runtime.
RUN useradd --create-home --uid 10001 app

COPY --from=builder --chown=app:app /app /app

# `WORKDIR` a créé le nœud `/app` en root : le rendre inscriptible par `app` et
# pré-créer `var/` (repli SQLite / checkpoints en l'absence de Postgres, ex.
# run standalone en dev — sous compose, Postgres est joignable et rien n'y est
# écrit).
RUN mkdir -p /app/var && chown app:app /app /app/var

USER app

EXPOSE 8000

# CMD = le workload réellement servi (uvicorn), pas la CLI (D6-09) : l'image
# déployée et le service `app` de docker-compose lancent la même chose. La CLI
# reste accessible via `docker compose exec app python -m velmo.cli`.
CMD ["uvicorn", "velmo.api:app", "--host", "0.0.0.0", "--port", "8000"]
