.PHONY: install lint type test test-unit test-int test-e2e ci up down migrate clean help

install: ## Install dependencies via uv
	uv sync

lint: ## Run ruff lint + format check
	uv run ruff check src tests
	uv run ruff format --check src tests

format: ## Auto-fix and format with ruff
	uv run ruff check --fix src tests
	uv run ruff format src tests

type: ## Run mypy strict type-check
	uv run mypy src

test-unit: ## Run unit tests
	uv run pytest tests/unit -v

coverage: ## Run tests with coverage report (terminal + HTML in htmlcov/)
	uv run pytest tests --cov --cov-report=term-missing --cov-report=html

test-int: ## Run integration tests
	uv run pytest tests/integration -v

test-e2e: ## Run E2E tests
	uv run pytest tests/e2e -v

test: ## Run full pytest suite
	uv run pytest tests -v

ci: ## Reproduce the GitHub Actions CI invocation locally (lint + type + unit-with-coverage + integration)
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy src
	uv run pytest tests/unit --cov=src/webhook_inspector/domain --cov=src/webhook_inspector/application --cov-fail-under=95 -q
	@docker info > /dev/null 2>&1 || { echo "❌ Docker not running — integration tests require testcontainers Postgres. Skip with: make ci-no-int"; exit 1; }
	uv run pytest tests/integration -q

ci-no-int: ## Same as `ci` but skips integration tests (use when Docker is unavailable)
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy src
	uv run pytest tests/unit --cov=src/webhook_inspector/domain --cov=src/webhook_inspector/application --cov-fail-under=95 -q

up: ## Start docker-compose stack
	docker compose up -d --build

down: ## Stop docker-compose stack and remove volumes
	docker compose down -v

dev-postgres: ## Start postgres in docker (for local dev with hot reload)
	docker compose up -d postgres
	@echo "Postgres ready. Set DATABASE_URL in your shell, e.g.:"
	@echo "  export DATABASE_URL=postgresql+psycopg://webhook:webhook@localhost:5434/webhook_inspector"

dev-app: ## Run app with uvicorn --reload (postgres must be up + DATABASE_URL set)
	uv run uvicorn webhook_inspector.web.app.main:app --reload --port 8000

dev-ingestor: ## Run ingestor with uvicorn --reload (postgres must be up + DATABASE_URL set)
	uv run uvicorn webhook_inspector.web.ingestor.main:app --reload --port 8001

dev: dev-postgres ## Start postgres + reminder to run dev-app / dev-ingestor in separate terminals
	@echo ""
	@echo "→ In a second terminal: make dev-app"
	@echo "→ In a third terminal:  make dev-ingestor"

migrate: ## Run alembic migrations
	uv run alembic upgrade head

clean: ## Run cleanup job (delete expired endpoints)
	uv run python -m webhook_inspector.jobs.cleaner

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'
