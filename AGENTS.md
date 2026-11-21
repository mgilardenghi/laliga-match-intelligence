# AGENTS.md

Instructions for coding agents that run, test or change this app.

## Read first

- [README.md](README.md): what the app does, how to run it and load the data.
- [docs/models.md](docs/models.md): the models, the backtest and the season simulation.
- [docs/dashboard.md](docs/dashboard.md): the dashboard pages, settings and browser tests.
- [docs/api.md](docs/api.md): the API endpoints.

## Run the app

```sh
cp .env.example .env
make up
```

Set `POSTGRES_PASSWORD` in `.env` before `make up`. The dashboard is at
<http://localhost:3000> and the API at <http://localhost:8000/docs>. Load data with
`make ingest-backfill`, `make backtest`, `make predict` and `make simulate`, in
that order. Only the ingestion commands use the network.

## Test the app

```sh
make setup
make lint
make test
make test-integration
make e2e
```

- `make setup` installs the Python (uv) and Node (pnpm) dependencies.
- `make lint` runs Ruff, mypy, ESLint and the TypeScript checks.
- `make test` runs the unit tests and needs no database.
- `make test-integration` runs the integration tests against a throwaway PostgreSQL container.
- `make e2e` runs the browser tests against a throwaway stack with no data; `make e2e-data` then `make e2e-loaded` run the tests that need data. `make e2e-down` removes that stack.
- After an API change, run `make api-client` and commit the regenerated client; `make api-client-check` fails when it is stale.

## Rules

- Code, docs and commit messages are in English.
- Never commit secrets, `.env` files, personal data or downloaded match data.
- Commit with your GitHub noreply address (`<id>+<login>@users.noreply.github.com`), set as the repository-local `user.email`. A personal address in a commit is public once the repository is public.
- Never add club crests, logos or other trademarked images.
- Models use only data available before kickoff. A feature or fit that can see a later match is a bug.
- Stored live predictions are immutable.
- The dashboard reads only the API. Only the API and the worker access the database.
- Schema changes go through a new Alembic migration; never edit an applied one.
- State the reason for any new dependency in the pull request.
- Work on a branch and open a pull request; never push to `main`.
- Commits follow Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `test:`).

## Done means

- `make lint`, `make test` and `make test-integration` pass.
- New behavior was checked by running the app, not only by unit tests.
- Docs are updated when behavior or commands change.
- The pull request lists what was verified and how.
