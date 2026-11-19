# Dashboard reference

The dashboard is the Next.js app in `apps/web`. This page covers how it reads
data, what the shell renders, which commands run it, and which environment
variables and browser tests it has. The data pages are listed under Routes below.

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
| `nav` | `Table`, `Matchday`, `Teams`, `Focus` and `Model performance`, in that order. The Focus entry reads `Focus: <team name>` when a team resolves and `Focus` alone when the API cannot answer, so the header still renders when the stack is down. |
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
| `/` | The current table of a season (`?season=`): the race at a glance (title, Champions League and relegation cards from the stored run), the table with end-of-season chances (`?columns=all` shows every column), and the projection (projected final points and the position heatmap). Below 60 rem the table becomes a list of tappable rows, and the heatmap is a hinted sideways scroller. Empty states name `make ingest` or `make simulate`. |
| `/matchday` | The fixtures of one matchday (`?matchday=`), one card per fixture, with the stored probabilities of every model and the market-implied probabilities. Empty states name `make ingest` or `make predict`. |
| `/matches/[matchId]` | One match: a header with each club's table place before kickoff and a one-sentence reading of the main forecast (Dixon-Coles, else Elo), every stored pre-kickoff forecast (fitted models first, then the market price when stored, then the baselines), both clubs' form, the head-to-head record, the season profile and, on a played fixture, the result. Empty states name `make predict`. |
| `/teams` | The clubs of one season (`?season=`) and the picker that stores the focus team (`?pick=focus`). |
| `/teams/[teamId]` | One club's season (`?season=`): season at a glance, home and away split, for and against, results, next fixtures and the focus form. Empty states name `make ingest` and `make simulate`. |
| `/focus` | Resolves the focus team and redirects to `/focus/<teamId>`; it renders nothing itself. |
| `/focus/[teamId]` | The team focus page: a story card that states the season in one sentence and beside it four headline tiles, then the season diagnosis, the relegation race, the next match with what each result would change, the multi-season trend, and a `How to read this page` block. The sticky section bar links to the four sections, and every metric carries a help control. |
| `/performance` | The backtest as a report: a verdict sentence computed on the common matches, the RPS ranking, the four scores with their definitions, the scores table (`?subset=all\|common`), RPS by season, calibration by outcome for one model (`?calibration=`), reproducibility, and the live predictions against the market. Empty states name `make backtest` and `make predict`. |
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
the form strip and the heatmap pick each label's ink (dark or white) from the
luminance of the fill it sits on (`components/charts/contrast.ts`), and the chart
gallery test checks every label-on-fill pair for 4.5:1 in both themes.

### Components and visual rules

The chart primitives are in `apps/web/components/charts` and the shared UI
components are in `apps/web/components/ui`. Every chart is a `ChartFigure`: one
`figcaption`, exactly one element carrying `role="img"` with the chart's
accessible name, and a `Data table` disclosure that repeats the numbers for a
screen reader and for export.

**No chart text lives inside a scaled SVG.** A chart's labels, axis ticks,
values and legends are HTML beside or over the marks, at a fixed size from the
type scale (12–14 px), so a chart reads the same on a laptop and on a phone and a
long team name wraps instead of being cut. What stays in an SVG draws marks only,
with `vector-effect: non-scaling-stroke`, and a plot has a maximum width and a
fixed height. Five drawings still hold their labels in an SVG — the projected
positions heatmap, the calibration panels, the form squares, and the two
per-match profiles on the club and match pages — and each of them renders at its
natural size (its CSS size equals its `viewBox`, with a scroll container where
that is wider than a phone), so nothing in them scales either. The two profiles
put each metric label above its pair of bars and are 300 px wide, so both sides
of the mirror fit a phone with nothing behind a sideways scroll, and each row has
its own scale because the metrics count different things. An assertion on the
gallery and one on the real pages measure the size a reader sees and fail if any
chart text leaves the 11–18 px band, and one more fails if a profile chart has
anything to scroll at 390 px.

Axes fit their data. `IntervalBars` rounds its domain out to the next nice step
of the rows it draws, so the axis ends within one step of the largest value
instead of at a fixed 0–200. `DotPlot` takes a `domains` prop and `LineChart` a
`domain` prop for an explicit axis, and both can leave a reference or a series
that falls far outside the others as an off-scale marker with its value printed,
instead of flattening every other mark into one dot.

One colour legend covers the whole app, in both themes: `--side-home` (home side,
the focus club), `--side-draw` (a draw, the league average), `--side-away` (away
side, the opponent), `--result-w`, `--result-d` and `--result-l` (a win, a draw
and a loss, and the Champions League, Europe and relegation places) and
`--zone-ucl`, `--zone-europe` and `--zone-relegation`. A colour means one thing
wherever it appears, and blue and orange differ in lightness as well as hue so
the legend still reads in greyscale. The result fills are identical in both
themes because the form strip picks a label's ink from the fill's hex fallback:
a token whose fallback is handed to `inkForFill` keeps fallback == token value
in every theme, and the comment in `globals.css` says so.

The five shared components are Server Components, so a page can use them without
adding a client bundle: `MetricHelp` (the ⓘ beside a metric name — a `<details>`
disclosure that also works with Enter, Space, a click and a tap), `HowToRead`
(the panel that closes a page with 2–4 term and definition pairs), `SectionNav`
(the sticky in-page anchor bar, which wraps on a phone), `StatTile` (one headline
number with its label, sub-line, an optional tone and an optional `MetricHelp`)
and `Legend` (the swatch row driven by the tokens above). They are exported from
`apps/web/components/ui` and each one is rendered in the `/dev/charts` gallery.

### Focus team

The focus team is resolved on the server, in this order: the `lmi_focus_team`
cookie when `GET /v1/teams/{id}` answers for it; otherwise the exact name in
`LMI_FOCUS_TEAM`, looked up in the teams of the current season; otherwise no
team, and `/focus` sends the reader to `/teams?pick=focus` instead of guessing.
The teams index stores the choice through a Server Action, which sets the cookie
for a year.

The story card at the top of `/focus/[teamId]` states the season in one sentence.
`apps/web/app/focus/[teamId]/story.ts` holds its rules: the band is the first
match of relegation 0.10, title 0.10, Champions League 0.25 and Europe 0.25, and
a clause appears only when its sample supports it — a priced match count equal to
the matches played, or both venues with at least three matches and a gap of at
least 0.75 points per match. A number the API does not have renders as an em
dash and never as zero, and every section reads the run its own payload names
rather than the season's latest run, so no page compares two simulations.

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
