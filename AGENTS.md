# AGENTS.md

Rules for any coding agent working in this repository.

## Read first

- `docs/spec/mvp.md`: scope and acceptance criteria.
- `docs/adr/`: accepted decisions. Do not contradict an accepted ADR. If a task needs a different decision, stop and report it.

## Hard rules

- All code, comments, docs and commit messages are in English.
- This repository is public. Never commit secrets, API keys, `.env` files, personal data, or raw downloaded data.
- Never add club crests, logos or other trademarked images.
- Models must use only data available before kickoff. Any feature or fit that could see future matches is a bug.
- Stored live predictions are immutable.
- Do not add dependencies outside ADR-0003 without stating why in the PR.
- Work on a branch per task. Never push to `main`.

## Layout

See ADR-0001. The dashboard talks only to the API. Only the API and worker access the database.

## Conventions

- Python: uv, Ruff, type hints everywhere, pytest.
- TypeScript: pnpm, strict mode.
- Migrations: Alembic only; never edit an applied migration.
- Commits: Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `test:`).

## Definition of done for a task

- Lint, type checks and tests pass locally.
- New behavior is verified by running it, not only by unit tests.
- Docs updated if behavior or commands changed.
- PR description lists what was verified and how.
