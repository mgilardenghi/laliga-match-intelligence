# Developer entry points for the LMI monorepo.
.DEFAULT_GOAL := help

COMPOSE := docker compose

.PHONY: help setup up down logs ps lint test build clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target>\n\n"} /^[a-z-]+:.*## / {printf "  %-8s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Create .env if missing and install Python and Node dependencies
	@if [ ! -f .env ]; then cp .env.example .env; fi
	uv sync --all-packages
	pnpm install --frozen-lockfile

up: ## Build and start the local Docker Compose stack, then wait for health
	# The worker is a one-shot job. `up --wait` fails on any container that
	# exits, even with status 0, so it starts once the services are healthy.
	$(COMPOSE) up -d --build --wait db redis api web
	$(COMPOSE) up -d worker

down: ## Stop the stack and keep its volumes
	$(COMPOSE) down

logs: ## Follow the logs of every service
	$(COMPOSE) logs -f

ps: ## Show the state of every service
	$(COMPOSE) ps

lint: ## Run Python and web linters, formatters and type checks
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy apps packages
	pnpm --filter web lint
	pnpm --filter web typecheck

test: ## Run the Python test suite
	uv run pytest

build: ## Build the web application and every Docker image
	pnpm --filter web build
	$(COMPOSE) build

clean: ## Stop the stack and delete its volumes (project lmi only)
	$(COMPOSE) down -v
