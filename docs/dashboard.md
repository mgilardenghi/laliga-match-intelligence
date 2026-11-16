# Dashboard reference

The dashboard is the Next.js app in `apps/web`. This page covers how it reads
data, what the shell renders, which commands run it, and which environment
variables and browser tests it has. The data pages are described nowhere yet:
they arrive in later M5 tasks.

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

## Shell

Every page renders inside one root layout, which owns the document, the theme,
the landmarks and the two boundaries around a page.

| Region | What it renders |
| --- | --- |
| `header` | The site name as a link, the navigation and the theme control. The site name is not an `h1`: each page owns its heading. |
| `nav` | `Table`, `Matchday`, `Teams`, `Focus` and `Performance`, in that order. The Focus entry reads `Focus: <team name>` when a team resolves and `Focus` alone when the API cannot answer, so the header still renders when the stack is down. |
| `main#main` | The page. The skip link targets it. A page must not render its own `main` element. |
| `footer` | The API status, the freshness of the data and the attribution. It never throws: a failed freshness read says so instead of failing the page. |

The states in `apps/web/components/states` are shared by every data page:
`EmptyState` names what is missing and the command that fills it, `ProblemState`
shows only a problem's `title` and `detail`, and `SectionSkeleton` is the
`Suspense` fallback. `app/not-found.tsx`, `app/error.tsx` and
`app/global-error.tsx` cover the three ways a page can fail. The error boundary
reads `API unreachable`, because every page reads the API while it renders and a
stack that is not running is the usual cause; its body leaves the other branch
open, since Next strips a server error's class and message in a production build
and the screen cannot know which failure it is showing. The digest under it is
the one fact the client still has, and it ties the screen to the server log.

### Routes

| Route | State |
| --- | --- |
| `/` | The M0 placeholder, until the table page lands. |
| `/teams` | The clubs of one season, and the picker that stores the focus team. |
| `/focus` | Resolves the focus team and redirects to `/focus/<teamId>`; it renders nothing itself. |
| `/dev/charts` | The chart gallery, behind `LMI_DEV_PAGES=1`. |

### Theme

The tokens live in `apps/web/app/globals.css`: surfaces, text, borders, the
focus ring, the categorical chart series, the form outcomes, the relegation
zone and the nine-step sequential scale. `:root` holds the light values, a
`prefers-color-scheme: dark` block overrides them, and `:root[data-theme]`
overrides both, so a stored choice wins over the system.

The root layout reads the `lmi_theme` cookie on the server and writes
`data-theme` into the opening `<html>` tag, so the first paint is already the
right theme and nothing has to be corrected in the browser. The theme control
cycles system, light and dark, writes the same cookie and sets the attribute.
The outcome fills and the sequential scale are identical in both themes, because
the form strip draws white letters on the outcome fills and the heatmap draws
fixed text colours on the sequential cells.

### Focus team

The focus team is resolved on the server, in this order: the `lmi_focus_team`
cookie when `GET /v1/teams/{id}` answers for it; otherwise the exact name in
`LMI_FOCUS_TEAM`, looked up in the teams of the current season; otherwise no
team, and `/focus` sends the reader to `/teams?pick=focus` instead of guessing.
The teams index stores the choice through a Server Action, which sets the cookie
for a year.

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
`uv run --group e2e pytest`. First-time setup:

1. `make setup` once. The throwaway Compose stack reads the same environment
   file as the local stack (placeholder values copied from the example file), and
   `make e2e` fails during Compose interpolation without it.
2. `uv run --group e2e playwright install chromium` installs the browser.
3. On a bare Linux or WSL machine the browser also needs system libraries
   (for example `libnspr4` and `libnss3`). Install them once with
   `uv run --group e2e playwright install-deps chromium`, which asks for
   administrator rights. CI installs its own browser and needs nothing from you.

The `E2E_PROJECT`, `E2E_API_PORT` and `E2E_WEB_PORT` overrides are checked by
`make e2e-guard` before any Docker command, and the checks are tested. The guard
stops names and ports that could reach the local stack. It does not claim to
sanitize a value that Make itself would expand (such as `$(shell ...)`): that
value comes from your own environment or command line, which already runs code as
you.

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

The generated client, the browser harness, the shell and the data layer are in
place: the workspace package, the Dockerfile and the Compose setup, the two test
tiers, the layout with its tokens, the shared states, the teams index and the
focus redirect, and this page. The data pages — the table, the matchday list,
the team and focus pages, the match preview and the performance page — arrive in
later M5 tasks, and so does the table page that replaces the `/` placeholder.
