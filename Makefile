# Developer entry points for the LMI monorepo.
.DEFAULT_GOAL := help

COMPOSE := docker compose

.PHONY: help setup up down logs ps ingest ingest-deps ingest-backfill backtest predict simulate scheduler e2e e2e-guard e2e-data e2e-data-load e2e-loaded e2e-down lint test test-integration build api-client api-client-check clean

# Throwaway database for the integration tests. Same image as Compose, a host
# port that is unlikely to collide, and placeholder credentials only.
TEST_DB := lmi-test-db
TEST_DB_IMAGE := postgres:18.7-alpine
TEST_DB_PORT := 55432

# Browser-test stack: its own Compose project and its own published ports, so it
# never touches the developer's `lmi` stack.
E2E_PROJECT ?= lmi-e2e
E2E_API_PORT ?= 18000
E2E_WEB_PORT ?= 13000
E2E_COMPOSE := docker compose -p $(E2E_PROJECT) -f docker-compose.yml -f e2e/compose.e2e.yml
E2E_API_URL := http://127.0.0.1:$(E2E_API_PORT)
E2E_BASE_URL := http://127.0.0.1:$(E2E_WEB_PORT)

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target>\n\n"} /^[a-z0-9-]+:.*## / {printf "  %-17s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Create .env if missing and install Python and Node dependencies
	@if [ ! -f .env ]; then cp .env.example .env; fi
	uv sync --all-packages
	pnpm install --frozen-lockfile

up: ## Build and start the local Docker Compose stack, then wait for health
	# The migration job is one-shot, and `up --wait` fails on any container that
	# exits even with status 0. So the long-running services are waited on in their
	# own call, with `--no-deps` because their dependencies are already started or
	# already finished here, and the exit code of the migration job is checked
	# explicitly. The worker is long-running as well: it consumes the job queue, so
	# it is started without `--wait` once the migration job has been waited on.
	$(COMPOSE) up -d --build --wait db redis
	# The migration job is built here like the other images: a changed migration or
	# seed must never be applied by the image already on disk.
	$(COMPOSE) up -d --build migrate
	$(COMPOSE) wait migrate
	$(COMPOSE) up -d --build --wait --no-deps api web
	$(COMPOSE) up -d --build --no-deps worker

down: ## Stop the stack and keep its volumes
	$(COMPOSE) down

logs: ## Follow the logs of every service
	$(COMPOSE) logs -f

ps: ## Show the state of every service
	$(COMPOSE) ps

ingest-deps:
	# `up` starts the migration job from the image already on disk, so a schema or
	# seed change would be applied by a stale image. The explicit `--build` here
	# and the wait on the job make the ingestion run against the current schema.
	$(COMPOSE) up -d --wait db redis
	$(COMPOSE) up -d --build migrate
	$(COMPOSE) wait migrate
	$(COMPOSE) build worker

ingest: ingest-deps ## Run the football-data and openfootball ingestion for the current season (network)
	$(COMPOSE) run --rm --no-deps worker python -m lmi_worker ingest $(ARGS)

ingest-backfill: ingest-deps ## Run the ingestion for every season from 2005-06 through the current one
	$(COMPOSE) run --rm --no-deps worker python -m lmi_worker ingest --backfill $(ARGS)

backtest: ingest-deps ## Run the walk-forward backtest and store its results
	$(COMPOSE) run --rm --no-deps worker python -m lmi_worker backtest $(ARGS)

predict: ingest-deps ## Run the live predictions for the current season and store them
	$(COMPOSE) run --rm --no-deps worker python -m lmi_worker predict $(ARGS)

simulate: ingest-deps ## Run a Monte Carlo season simulation and store its results
	$(COMPOSE) run --rm --no-deps worker python -m lmi_worker simulate $(ARGS)

scheduler: ## Start the Celery beat scheduler (profile "scheduler"; its daily cycle downloads data)
	$(COMPOSE) --profile scheduler up -d --build scheduler

e2e-guard:
	@# A wrong value here addresses real data: `down -v` on the developer's own
	@# `lmi` project would delete their volumes, and a reserved port collides with
	@# the local stack. So every override is validated before any Docker command.
	@# The project name is also checked against Compose project-name syntax, because
	@# it is interpolated into a shell command: the exact string `lmi` is not the
	@# only value that could reach that project (`lmi ` would), and a value holding
	@# shell syntax would rewrite the command instead of naming a project.
	@# The checks cover shell metacharacters, not Make-level expansion: a value such
	@# as `$$(shell ...)` is expanded by Make before the recipe runs. That value can
	@# only come from the caller's own environment or command line, which already
	@# has the caller's rights, so it is not a boundary this guard defends.
	@case "$(E2E_PROJECT)" in \
		''|lmi) echo "e2e-guard: E2E_PROJECT must not be empty or 'lmi' (got '$(E2E_PROJECT)')" >&2; exit 1 ;; \
	esac
	@case "$(E2E_PROJECT)" in \
		[-_]*|*[!a-z0-9_-]*) \
			echo "e2e-guard: E2E_PROJECT must use lowercase letters, digits, '-' and '_' (got '$(E2E_PROJECT)')" >&2; \
			exit 1 ;; \
	esac
	@for port in "$(E2E_API_PORT)" "$(E2E_WEB_PORT)"; do \
		case "$$port" in \
			''|*[!0-9]*) echo "e2e-guard: port '$$port' must be numeric" >&2; exit 1 ;; \
		esac; \
		if [ "$$port" -lt 1024 ] || [ "$$port" -gt 65535 ]; then \
			echo "e2e-guard: port '$$port' must be between 1024 and 65535" >&2; \
			exit 1; \
		fi; \
		for reserved in 8000 3000 8156 18004 18005 18006 $$(seq 55432 55463); do \
			if [ "$$port" -eq "$$reserved" ]; then \
				echo "e2e-guard: port '$$port' is reserved" >&2; \
				exit 1; \
			fi; \
		done; \
	done
	@if [ "$(E2E_API_PORT)" -eq "$(E2E_WEB_PORT)" ]; then \
		echo "e2e-guard: E2E_API_PORT and E2E_WEB_PORT must differ" >&2; \
		exit 1; \
	fi

e2e: e2e-guard ## Build the throwaway browser-test stack and run the tests that need no data
	$(E2E_COMPOSE) up -d --build --wait db redis
	$(E2E_COMPOSE) up -d --build migrate
	$(E2E_COMPOSE) wait migrate
	$(E2E_COMPOSE) up -d --build --wait --no-deps api web
	E2E_BASE_URL=$(E2E_BASE_URL) uv run --group e2e pytest -o addopts="" e2e -m empty

e2e-data: e2e-guard ## Load the throwaway browser-test stack with real data (network, idempotent)
	@# `exit` ends one recipe's shell, not the target, so a skip written as a plain
	@# `if ... exit 0; fi` falls through to the next line and loads anyway. The
	@# loading therefore lives in its own target, invoked only when it is needed.
	@if curl -fsS $(E2E_API_URL)/v1/model-metrics >/dev/null 2>&1; then \
		echo "the e2e stack already holds data"; \
	else \
		$(MAKE) e2e-data-load; \
	fi

e2e-data-load: e2e-guard
	# The loaded tier needs the API published, so the data services are started
	# here in the same shape as in the `e2e` target before anything is loaded.
	$(E2E_COMPOSE) up -d --build --wait db redis
	$(E2E_COMPOSE) up -d --build migrate
	$(E2E_COMPOSE) wait migrate
	$(E2E_COMPOSE) up -d --build --wait --no-deps api
	$(MAKE) ingest-backfill backtest predict simulate COMPOSE="$(E2E_COMPOSE)"

e2e-loaded: e2e-guard ## Run the tests that need data against a standalone dashboard server
	pnpm --filter web build
	@rm -rf apps/web/.next/standalone/apps/web/public apps/web/.next/standalone/apps/web/.next/static
	@mkdir -p apps/web/.next/standalone/apps/web/.next
	@cp -R apps/web/.next/static apps/web/.next/standalone/apps/web/.next/static
	@cp -R apps/web/public apps/web/.next/standalone/apps/web/public
	# Address only the throwaway project: the loaded tier serves the build from
	# this worktree, so the Compose `web` service must not answer on the port. A
	# silent stop failure would leave that service answering for the build, so the
	# port is probed before the server starts.
	@$(E2E_COMPOSE) stop web
	@if curl -fsS $(E2E_BASE_URL) >/dev/null 2>&1; then \
		echo "something still answers on $(E2E_BASE_URL) after stopping the throwaway web service" >&2; \
		exit 1; \
	fi
	@log=$$(mktemp); \
	PORT=$(E2E_WEB_PORT) HOSTNAME=127.0.0.1 API_URL=$(E2E_API_URL) node apps/web/.next/standalone/apps/web/server.js >$$log 2>&1 & \
	server=$$!; \
	trap 'kill $$server 2>/dev/null || true; rm -f "$$log"' EXIT INT TERM; \
	ready=0; \
	for attempt in $$(seq 1 60); do \
		if ! kill -0 $$server 2>/dev/null; then \
			echo "the standalone server exited before it answered on $(E2E_WEB_PORT)" >&2; \
			cat $$log >&2; \
			exit 1; \
		fi; \
		if curl -fsS $(E2E_BASE_URL) >/dev/null 2>&1; then ready=1; break; fi; \
		sleep 1; \
	done; \
	if [ $$ready -eq 0 ]; then \
		echo "the standalone server did not answer on $(E2E_WEB_PORT)" >&2; \
		cat $$log >&2; \
		exit 1; \
	fi; \
	E2E_BASE_URL=$(E2E_BASE_URL) uv run --group e2e pytest -o addopts="" e2e -m loaded

e2e-down: e2e-guard ## Stop the throwaway browser-test stack and delete its volumes
	$(E2E_COMPOSE) down -v --remove-orphans

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
	@LMI_ALLOW_DESTRUCTIVE_TESTS=1 POSTGRES_HOST=localhost POSTGRES_PORT=$(TEST_DB_PORT) POSTGRES_USER=lmi \
		POSTGRES_PASSWORD=placeholder POSTGRES_DB=lmi \
		uv run pytest -m integration; \
		status=$$?; docker rm -f $(TEST_DB) >/dev/null 2>&1; exit $$status

build: ## Build the web application and every Docker image
	pnpm --filter web build
	$(COMPOSE) build

api-client: ## Regenerate the typed API client from the OpenAPI contract
	uv run python -m lmi_api.openapi_export packages/api-client/openapi.json
	pnpm --filter @lmi/api-client generate

api-client-check: ## Fail when the committed API client no longer matches the API
	$(MAKE) api-client
	git diff --exit-code -- packages/api-client

clean: ## Stop the stack and delete its volumes (project lmi only)
	$(COMPOSE) down -v
	@docker rm -f $(TEST_DB) >/dev/null 2>&1 || true
