# Developer entry points for the LMI monorepo.
.DEFAULT_GOAL := help

COMPOSE := docker compose

.PHONY: help setup up down logs ps lint test test-integration build clean

# Throwaway database for the integration tests. Same image as Compose, a host
# port that is unlikely to collide, and placeholder credentials only.
TEST_DB := lmi-test-db
TEST_DB_IMAGE := postgres:18.7-alpine
TEST_DB_PORT := 55432

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target>\n\n"} /^[a-z-]+:.*## / {printf "  %-8s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Create .env if missing and install Python and Node dependencies
	@if [ ! -f .env ]; then cp .env.example .env; fi
	uv sync --all-packages
	pnpm install --frozen-lockfile

up: ## Build and start the local Docker Compose stack, then wait for health
	# The migration job and the worker are one-shot, and `up --wait` fails on any
	# container that exits even with status 0. So the long-running services are
	# waited on in their own call, with `--no-deps` because their dependencies
	# are already started or already finished here, and the exit code of the
	# migration job is checked explicitly.
	$(COMPOSE) up -d --build --wait db redis
	$(COMPOSE) up -d migrate
	$(COMPOSE) wait migrate
	$(COMPOSE) up -d --build --wait --no-deps api web
	$(COMPOSE) up -d --build --no-deps worker

down: ## Stop the stack and keep its volumes
	$(COMPOSE) down

logs: ## Follow the logs of every service
	$(COMPOSE) logs -f

ps: ## Show the state of every service
	$(COMPOSE) ps

lint: ## Run Python and web linters, formatters and type checks
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy apps/api apps/worker packages
	pnpm --filter web lint
	pnpm --filter web typecheck

test: ## Run the Python unit test suite (no database needed)
	uv run pytest -m "not integration"

test-integration: ## Run the integration tests against a throwaway PostgreSQL container
	# The container is disposable: placeholder credentials, a dedicated port, and
	# it is always removed, including when pytest fails.
	@docker rm -f $(TEST_DB) >/dev/null 2>&1 || true
	docker run -d --name $(TEST_DB) -p $(TEST_DB_PORT):5432 \
		-e POSTGRES_USER=lmi -e POSTGRES_DB=lmi -e POSTGRES_PASSWORD=placeholder \
		$(TEST_DB_IMAGE)
	@for attempt in $$(seq 1 60); do \
		if docker exec $(TEST_DB) pg_isready -q -h 127.0.0.1 -U lmi -d lmi 2>/dev/null; then exit 0; fi; \
		sleep 1; \
	done; \
	echo "the test database did not become ready" >&2; docker rm -f $(TEST_DB) >/dev/null; exit 1
	@POSTGRES_HOST=localhost POSTGRES_PORT=$(TEST_DB_PORT) POSTGRES_USER=lmi \
		POSTGRES_PASSWORD=placeholder POSTGRES_DB=lmi \
		uv run pytest -m integration; \
		status=$$?; docker rm -f $(TEST_DB) >/dev/null 2>&1; exit $$status

build: ## Build the web application and every Docker image
	pnpm --filter web build
	$(COMPOSE) build

clean: ## Stop the stack and delete its volumes (project lmi only)
	$(COMPOSE) down -v
	@docker rm -f $(TEST_DB) >/dev/null 2>&1 || true
