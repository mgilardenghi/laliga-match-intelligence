# Dashboard reference

The dashboard is the Next.js app in `apps/web`. This page covers how it reads
data, which commands run it, and which environment variables and browser tests
it has. The pages themselves are described nowhere yet: they arrive in later M5
tasks.

## Read path

The dashboard is server-rendered. Every page is a Server Component that reaches
the API over the loopback interface, or over the Compose network when the app
runs in the stack, and it renders the answer into the HTML it sends. The
browser therefore never calls the API: there is no CORS configuration between
the two services and no `NEXT_PUBLIC_*` variable in the app. No page touches
the database either; the API is the only entry point to LMI data.

The client is generated, not written. `packages/api-client/openapi.json` is the
committed OpenAPI snapshot, `make api-client` turns it into typed paths and
components, and the app imports the result as `@lmi/api-client`. A call such as
`client.GET("/v1/seasons")` is typed by that snapshot, so a contract change
surfaces as a type error in the dashboard instead of as a runtime surprise.

## Commands

| Command | What it does |
| --- | --- |
| `make api-client` | Regenerate the typed client from the API, writing the OpenAPI snapshot and the generated types. |
| `make api-client-check` | Regenerate the client and fail when the committed snapshot differs from what the API produces now. |
| `pnpm --filter web dev` | Run the dashboard in development mode against `API_URL`. |
| `pnpm --filter web build` | Build the dashboard, including its server-side type checking. |
| `make up` | Build and start the local Docker Compose stack and wait for it to be healthy. |
| `make e2e` | Build and start the throwaway stack and run the browser tests marked `empty`. It leaves the stack up for inspection. |
| `make e2e-data` | Build and start the throwaway data services (db, redis, the migration job and the API), then load them once. It is idempotent: it skips loading when `GET /v1/model-metrics` already answers `200`. |
| `make e2e-loaded` | Build the dashboard, stop the throwaway stack's `web` service so it cannot answer on the port, then start the standalone server on `E2E_WEB_PORT` and run the browser tests marked `loaded`. |
| `make e2e-down` | Stop the throwaway stack and delete its volumes. |

## Environment variables

| Variable | Meaning |
| --- | --- |
| `API_URL` | API base URL read on the server. Default `http://localhost:8000`; the Compose stack sets `http://api:8000`. |
| `LMI_FOCUS_TEAM` | Team the focus page defaults to. Default `Valencia`, spelled as the stored canonical name. |
| `LMI_DEV_PAGES` | `1` exposes the chart development pages. The browser-test stack sets it. |
| `E2E_PROJECT` | Compose project of the throwaway stack. Default `lmi-e2e`. |
| `E2E_API_PORT` | Host port of the throwaway API. Default `18000`. |
| `E2E_WEB_PORT` | Host port of the throwaway dashboard. Default `13000`. |
| `E2E_BASE_URL` | Base URL the browser tests open. Default `http://127.0.0.1:$E2E_WEB_PORT`. |
| `E2E_SCREENSHOTS` | `1` writes light and dark screenshots at 390 px and 1280 px into `e2e/artifacts/`, which is gitignored. |

## Browser tests

The suite lives in `e2e/` and runs with Playwright through
`uv run --group e2e pytest`. First-time setup installs the browser:
`uv run --group e2e playwright install chromium`.

There are two tiers. `empty` runs against a stack with no data and is the only
tier CI runs. `loaded` runs against real ingested data and is local only.

The throwaway stack is its own Compose project on its own ports, so it never
touches the user's `lmi` stack.

The three `E2E_*` values are validated before any Docker command runs: the
project must differ from `lmi` and be a Compose project name (lowercase letters,
digits, `-` and `_`), and the ports must be numeric, distinct and outside the
reserved ranges. An invalid value fails before a container is touched.

Two rules hold for every test. A test asserts structure, never values: it
checks that a page renders the region, the column or the state it should, not
the numbers a particular dataset happens to produce. And every test fails when
the browser requests the API port directly, because the browser must talk only
to the dashboard.

## Contract drift

`make api-client-check` regenerates the client and fails when the committed
snapshot differs from the regenerated one. The `api-client` CI job runs it on
every pull request, so an API change and a stale client cannot be merged
together.

## Status

This task ships the generated client, the browser harness and the
infrastructure around them: the workspace package, the Dockerfile and the
Compose setup, the two test tiers, and this page. The pages themselves, and the
code that reads `LMI_FOCUS_TEAM` and `LMI_DEV_PAGES`, arrive in later M5 tasks.
