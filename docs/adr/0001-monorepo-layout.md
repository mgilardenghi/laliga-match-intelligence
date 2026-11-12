# ADR-0001: One monorepo for the whole product

Status: accepted (2026-11-12)

## Context

The product has a dashboard, an API, an ingestion and simulation worker, and local infrastructure. Later phases add a RAG service and Kubernetes/Terraform. There is one product and one developer.

## Decision

Use one repository with this layout:

```
apps/web/        Next.js dashboard (reads data only through the API)
apps/api/        FastAPI service (only entry point to the data)
apps/worker/     Python ingestion, model fitting, simulation jobs
packages/        shared code (e.g. generated API client, shared Python domain package)
infra/           docker-compose now; terraform/ and helm/ later
docs/adr/        architecture decisions
docs/spec/       feature specifications
```

A separate platform repository is created only if the infrastructure becomes a reusable template for several apps.

## Consequences

- One clone and one `docker compose up` show the whole system.
- One CI pipeline with path filters per app.
- Python (uv workspace) and TypeScript (pnpm) tooling coexist in one repo.
