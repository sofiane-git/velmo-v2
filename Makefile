.PHONY: install up down migrate seed seed-kb chat eval ci test fmt fmt-check lint lint-imports typecheck acceptance eval-gate

install:
	uv sync

up:
	docker compose up

down:
	docker compose down

migrate:
	uv run alembic upgrade head

seed:
	uv run python scripts/seed.py

seed-kb:
	uv run python scripts/seed_kb.py

chat:
	uv run python -m velmo.cli

eval:
	uv run python -m velmo.mlops.score

# Parité locale/CI : mêmes étapes, même ordre que .github/workflows/quality.yml
# (audit D10-07 — `make ci` doit rejouer exactement le gate).
ci: lint fmt-check typecheck lint-imports acceptance eval-gate

test:
	uv run pytest tests/ -v

fmt:
	uv run ruff format .
	uv run ruff check --fix .

fmt-check:
	uv run ruff format --check src tests

lint:
	uv run ruff check src tests

lint-imports:
	uv run lint-imports --config pyproject.toml

typecheck:
	uv run mypy src

acceptance:
	uv run pytest tests/acceptance/ -v

eval-gate:
	uv run python -m velmo.mlops.score --min-score 0.8
