# ADR-0003: Technology stack

Status: accepted (2026-11-12)

## Context

Target roles are backend, platform and AI engineering in Europe. Job offers ask for Python, PostgreSQL, Docker, REST/OpenAPI and CI. The project must run on a 16 GB laptop (WSL2) at no cost.

## Decision

| Area | Choice |
|---|---|
| Backend language | Python 3 (latest stable supported by all dependencies) |
| Python tooling | uv (workspace, lockfile), Ruff (lint and format), mypy or pyright (types), pytest |
| API | FastAPI, Pydantic v2 |
| Database access | SQLAlchemy 2.x, Alembic migrations |
| Database | PostgreSQL |
| Cache and job broker | Redis |
| Modeling | NumPy, SciPy, pandas (or polars); no deep learning in the MVP |
| Frontend | Next.js with TypeScript, pnpm, client generated from OpenAPI |
| Local runtime | Docker Compose with multi-stage images |
| CI | GitHub Actions |

Exact versions are pinned in lockfiles and image tags during M0.

## Consequences

- Effect, Astro and GraphQL are not used in this project.
- The frontend stays thin: no business logic, no direct database access.
