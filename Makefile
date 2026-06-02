.PHONY: up down build test lint typecheck db-migrate db-reset install run logs

# ── Docker ───────────────────────────────────────────────────────────────────
up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f api

# ── Dev local ────────────────────────────────────────────────────────────────
install:
	uv pip install -e ".[dev]"

run:
	uvicorn middleware.api.main:app --host 0.0.0.0 --port 8000 --reload

# ── Qualité ──────────────────────────────────────────────────────────────────
lint:
	uv run ruff check src/ tests/

lint-fix:
	uv run ruff check --fix src/ tests/

typecheck:
	uv run mypy src/

test:
	uv run pytest tests/ -v --cov=src/middleware --cov-report=term-missing

# ── Base de données ───────────────────────────────────────────────────────────
db-migrate:
	uv run alembic upgrade head

db-rollback:
	uv run alembic downgrade -1

db-reset:
	docker compose exec postgres psql -U middleware -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
	uv run alembic upgrade head
