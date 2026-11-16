# LMI API reference

The FastAPI service in `apps/api` is the only entry point to LMI data: the
dashboard reads nothing else, and its client is generated from this
specification. The OpenAPI document is served at `/openapi.json`, and the two
health routes stay at the root (`/health`, `/version`).

Everything below is served under the `/v1` prefix. The two conventions worth
reading first are the error format and the pagination envelope; the rest is one
section per resource.

## Conventions

### Errors

Every error response is an RFC 9457 problem details object, sent with the media
type `application/problem+json`:

```json
{
  "type": "about:blank",
  "title": "Not Found",
  "status": 404,
  "detail": "Match not found",
  "errors": null
}
```

| Field | Meaning |
| --- | --- |
| `type` | Always `about:blank`: the API publishes no problem type URIs. The status and the title carry the meaning. |
| `title` | The HTTP status phrase. |
| `status` | The status code, repeated in the body. |
| `detail` | A sentence written by the API, never a stack trace or a SQL fragment. The matches, standings, model-metrics, ingestion-runs and simulations routes use a fixed sentence that never repeats a value the client sent; the older seasons and teams routes quote the value they were given, which a follow-up task unifies. |
| `errors` | Present only on a validation failure: one entry per rejected value with `loc`, `msg` and `type`. The rejected value itself is never echoed. |

The statuses a route can return are declared in its OpenAPI responses, so the
generated client models the real body instead of a default error envelope:

| Route | `404` | `422` | `503` |
| --- | --- | --- | --- |
| `GET /v1/seasons` | — | — | — |
| `GET /v1/teams` | unknown season code | declared, not reachable (its only parameter is an unconstrained string) | — |
| `GET /v1/teams/{team_id}` | unknown team | non-integer identifier | — |
| `GET /v1/matches` | unknown season code or team identifier | a query value outside its rule | — |
| `GET /v1/matches/{match_id}` | unknown match | non-integer identifier | — |
| `GET /v1/standings` | unknown season code, or no season with matches | malformed `as_of` | — |
| `GET /v1/model-metrics` | `no backtest run`: no succeeded run is stored, or `run_id` names one that is missing, running or failed | malformed `run_id` | — |
| `GET /v1/ingestion-runs` | unknown season code | a `source`, `status`, `limit` or `offset` value outside its rule | — |
| `GET /v1/ingestion-runs/{run_id}` | unknown ingestion run | non-UUID identifier | — |
| `POST /v1/simulations` | unknown season code, or no season has a match while the body names none | a body value outside its bounds, or an `as_of_date` later than the Madrid date | the queue refused the message |
| `GET /v1/simulations` | unknown season code | a `status`, `kind`, `limit` or `offset` value outside its rule | — |
| `GET /v1/simulations/latest` | `no succeeded simulation` | declared, not reachable (its only parameter is an unconstrained string) | — |
| `GET /v1/simulations/history` | unknown season code, unknown team identifier, or no season with a run | non-integer `team_id` | — |
| `GET /v1/simulations/{run_id}` | unknown simulation run | non-UUID identifier | — |

An unexpected failure is a `500` whose body carries no detail: the cause is in
the server log.

A route uses the shared problem response set, so `GET /v1/teams` and
`GET /v1/simulations/latest` declare a `422` in the OpenAPI document even though
no validation failure can occur there: their only parameter is an unconstrained
string, and an unknown season code is a `404`.

### Pagination

Collections that grow without bound return a page envelope:

```json
{
  "items": [],
  "total": 0,
  "limit": 100,
  "offset": 0
}
```

`total` is the number of rows the filter matched, not the size of `items`; a
client renders the result count and requests the next page from it. `limit` and
`offset` are echoed as applied.

## Seasons

### `GET /v1/seasons`

Every stored season, most recent first.

```json
[{ "code": "2025-26", "start_year": 2025 }]
```

`code` is the human-readable season identifier and `start_year` the calendar
year the season began in; the seasons run from `2005-06` to `2030-31`.

## Teams

### `GET /v1/teams`

Every stored team, or the teams of one season.

| Parameter | Type | Default | Rule |
| --- | --- | --- | --- |
| `season` | string | none (all teams) | Season code. A season whose fixtures are not loaded yet is an empty list with `200`, not an error; only an unknown code is a `404`. |

The optional season filter returns the teams that appear as the home or the away
side in at least one match of that season, whatever the match status. Rows are
ordered by name, with the identifier as tie-break.

### `GET /v1/teams/{team_id}`

One team: `{ "id": 1, "name": "Alavés" }`. An unknown identifier is a `404`.

## Matches

### `GET /v1/matches`

One page of fixtures.

| Parameter | Type | Default | Rule |
| --- | --- | --- | --- |
| `season` | string | none (all seasons) | Season code. An unknown code is a `404`. |
| `team_id` | integer | none | Only fixtures where the team played home **or** away. An unknown identifier is a `404`. |
| `matchday` | integer | none | `1`–`46`. Outside the range is a `422`. |
| `status` | `scheduled` \| `played` | none | Any other value is a `422`. |
| `date_from` | date (`YYYY-MM-DD`) | none | Inclusive lower bound on `kickoff_date`. |
| `date_to` | date (`YYYY-MM-DD`) | none | Inclusive upper bound on `kickoff_date`. A `date_from` after `date_to` is a `422`. |
| `order` | `asc` \| `desc` | `asc` | Applies to the whole sort key. |
| `limit` | integer | `100` | `1`–`500`. |
| `offset` | integer | `0` | `>= 0`. |

Every filter is optional and the ones given are combined with `AND`. The date
range is compared on `kickoff_date` only, so a range covers whole days and a
fixture without a published kickoff time is still inside it.

The sort key is `kickoff_date`, then `kickoff_time`, then `id`. A fixture whose
source published no time never jumps to the top of its day and never leads the
list in reverse: a null `kickoff_time` sorts last in **both** directions, while
the date and the identifier still reverse. Within one day `desc` reverses the
relative order of the timed fixtures and still ends with the untimed ones, so
the two orders are identical on a day that holds a single timed fixture and
diverge as soon as one day holds two. A client cannot page `desc` by reversing
`asc`.

```json
{
  "items": [
    {
      "id": 7980,
      "season": "2025-26",
      "kickoff_date": "2026-05-24",
      "kickoff_time": "20:00:00",
      "matchday": null,
      "status": "played",
      "home_team": { "id": 40, "name": "Villarreal" },
      "away_team": { "id": 4, "name": "Atlético Madrid" },
      "home_goals": 5,
      "away_goals": 1,
      "ht_home_goals": 4,
      "ht_away_goals": 1,
      "result": "H"
    }
  ],
  "total": 380,
  "limit": 100,
  "offset": 0
}
```

`null` in a nullable field means "not known yet", never zero: a `scheduled`
fixture carries no goals, no half-time score and no result. `result` is `H`, `D`
or `A` from the home side's point of view. `kickoff_time` is the local time of
the source, with no timezone attached.

### `GET /v1/matches/{match_id}`

One fixture, in the collection shape plus its statistics.

```json
{
  "id": 7980,
  "season": "2025-26",
  "kickoff_date": "2026-05-24",
  "kickoff_time": "20:00:00",
  "matchday": null,
  "status": "played",
  "home_team": { "id": 40, "name": "Villarreal" },
  "away_team": { "id": 4, "name": "Atlético Madrid" },
  "home_goals": 5,
  "away_goals": 1,
  "ht_home_goals": 4,
  "ht_away_goals": 1,
  "result": "H",
  "stats": {
    "home_shots": 14,
    "away_shots": 9,
    "home_shots_on_target": 8,
    "away_shots_on_target": 4,
    "home_fouls": 3,
    "away_fouls": 12,
    "home_corners": 5,
    "away_corners": 9,
    "home_yellow": 0,
    "away_yellow": 1,
    "home_red": 0,
    "away_red": 0,
    "home_xg": null,
    "away_xg": null
  }
}
```

`stats` is `null` when the match has no statistics row, which is the normal
state of a fixture that has not been played. It is not an object of null fields:
the client has to know whether statistics exist before it renders a section for
them. Inside `stats` a single `null` means that one column is unknown, which is
the normal state of the expected goals: the sources this database ingests from
publish shots, fouls, corners and cards, and not every one of them publishes
`xg`. `home_xg` and `away_xg` are JSON numbers, not strings. An unknown
identifier is a `404`; a non-integer one is a `422`.

## Standings

### `GET /v1/standings`

The league table of one season, computed from played matches.

| Parameter | Type | Default | Rule |
| --- | --- | --- | --- |
| `season` | string | the current season (see below) | Season code. An unknown code is a `404`. |
| `as_of` | date (`YYYY-MM-DD`) | none (the whole season) | Count only matches played on or before this date, inclusive. An invalid date is a `422`. |

**Which season by default.** Without `season`, the answer is the greatest
`start_year` among the seasons that have at least one match, whatever its
status. When no season has any match the response is a `404`. A season that
exists but has no fixtures returns `200` with `rows: []` and
`matches_counted: 0`: there are no teams to rank, because the team set comes from
the season's matches.

**Which matches are counted.** Only `status = "played"` matches of the season,
and, when `as_of` is given, only those with `kickoff_date <= as_of`. The cut-off
is inclusive. A postponed match keeps its original `scheduled` status and a past
date, and is not counted.

**Which teams are ranked.** Every team that appears as the home or the away side
in any match of the season, whatever the status. A team whose only fixture is
still scheduled is therefore in the table with zeros.

`matches_counted` is the number of results the table was computed from, so a
client can tell "matchday 1 of 38" from "the season is over" without counting
rows.

The final table of 2024-25, first row only:

```json
{
  "season": "2024-25",
  "as_of": null,
  "matches_counted": 380,
  "rows": [
    {
      "position": 1,
      "team": { "id": 5, "name": "Barcelona" },
      "played": 38,
      "won": 28,
      "drawn": 4,
      "lost": 6,
      "goals_for": 102,
      "goals_against": 39,
      "goal_difference": 63,
      "points": 88,
      "form": ["W", "L", "W", "W", "W"]
    }
  ]
}
```

A win is worth three points and a draw one. `form` holds the last five outcomes,
most recent first, as `W`, `D` or `L`; before five matches are played it is
shorter, and before any it is empty. `position` runs `1..n` with no shared
place.

### Tie-break order

Rows are ordered by these rules, top to bottom; the first one that separates two
teams decides.

1. Points, descending.
2. Head-to-head, inside a group of teams level on points, and only when the group
   is complete, meaning every ordered pair inside it has been played. A two-team
   group compares head-to-head goal difference; a larger group compares
   mini-league points and then mini-league goal difference, both computed over
   the whole group.
3. Goal difference, descending.
4. Goals scored, descending.
5. Team name in Unicode code point order, then team identifier.

Away goals are never used, at any step.

### Deviations from the official regulation

The competition's own regulations order teams by points, head-to-head points,
head-to-head goal difference, goal difference, goals scored and finally
fair-play points. This table reproduces that chain except in two places:

- **Fair play is not a tie-break.** The table stops at step 5 above, so a tie
  that survives goals scored can be ordered differently from the official one.
- **Head-to-head is not recursive.** When a mini-league separates part of a
  group and leaves the rest level, the remaining teams are not compared again
  with fresh head-to-head totals; they fall through to goal difference, goals
  scored, name and identifier like any other tie.

Point deductions are not modelled either: the schema carries no deduction
column, so the table is a pure function of the stored results.

## Model metrics

### `GET /v1/model-metrics`

The stored report of one walk-forward backtest: the header of the run, the
models it scored, their metrics and their calibration bins. Without `run_id`
the latest succeeded run is served, which is the one a dashboard opens on.

| Parameter | Type | Default | Rule |
| --- | --- | --- | --- |
| `run_id` | UUID | none (the latest succeeded run) | Selects one stored run. A value that is not a UUID is a `422`. |

Only a succeeded run is served. The `404` carries the detail `no backtest run`
for every miss: no run is stored, `run_id` names no run, or it names one that is
still `running` or has `failed`. The response therefore never discloses which
identifiers are in flight.

```json
{
  "run": {
    "id": "6f1b1c0e-6f4a-4c1e-9d3e-2b1a7c5f8a10",
    "started_at": "2026-11-15T20:04:11Z",
    "finished_at": "2026-11-15T20:19:37Z",
    "seed": 0,
    "from_year": 2015,
    "step_days": 7,
    "code_version": "0.4.0+3a1f0c9",
    "data_fingerprint": "5d0f7b0c9e2a4f31",
    "n_matches": 3420
  },
  "models": [
    { "name": "home_win", "kind": "baseline", "version": "1", "hyperparameters": {} },
    { "name": "market", "kind": "market", "version": "1", "hyperparameters": { "seed": 0 } },
    {
      "name": "dixon_coles",
      "kind": "model",
      "version": "1",
      "hyperparameters": {
        "seed": 0,
        "time_decay_xi": 0.0019,
        "window_days": 1095,
        "l2_penalty": 0.01,
        "rho_bound": 0.25,
        "max_goals": 10
      }
    }
  ],
  "metrics": [
    {
      "model": "dixon_coles",
      "season_code": null,
      "subset": "all",
      "n_matches": 342,
      "n_fixtures": 360,
      "coverage": 0.95,
      "log_loss": 0.9812,
      "brier": 0.5721,
      "rps": 0.1834,
      "accuracy": 0.5311
    },
    {
      "model": "dixon_coles",
      "season_code": null,
      "subset": "common",
      "n_matches": 324,
      "n_fixtures": 360,
      "coverage": 0.9,
      "log_loss": 0.9744,
      "brier": 0.5688,
      "rps": 0.1801,
      "accuracy": 0.537
    },
    {
      "model": "market",
      "season_code": null,
      "subset": "common",
      "n_matches": 324,
      "n_fixtures": 360,
      "coverage": 0.9,
      "log_loss": 0.9607,
      "brier": 0.5602,
      "rps": 0.1772,
      "accuracy": 0.5463
    }
  ],
  "calibration": [
    {
      "model": "dixon_coles",
      "outcome": "H",
      "bin_index": 0,
      "bin_lower": 0,
      "bin_upper": 0.01,
      "n": 41,
      "mean_predicted": 0.0063,
      "observed_frequency": 0
    }
  ]
}
```

**The two subsets.** `metrics` holds one row per model, season and subset.
`subset` is `all`, every forecast the model made over the fixtures of the scored
window, or `common`, the fixtures every model scored, so a comparison between
two models uses the same matches. `n_matches` is the number of forecasts behind
the row, `n_fixtures` the number of fixtures it covers and `coverage` their
ratio: a model that reports no forecast for a fixture lowers its own coverage.
`season_code` is `null` on the overall row and a season code on a per-season
row.

**The market reference.** `kind` states each model's role: `baseline` for the
trivial predictors, `market` for the bookmaker's numbers and `model` for the
fitted ones. The `market` row is the reference every other model has to beat:
the probabilities implied by the pre-closing decimal odds with the margin
removed, computed with nothing fitted, and it reports no forecast for a fixture
that carries no odds. Its `common` row is the number to compare a fitted model's
`common` row with.

`log_loss`, `brier` and `rps` are errors, so lower is better; `accuracy` is a hit
rate, so higher is better. Each calibration row is one bin of one outcome of one
model, `outcome` being `H`, `D` or `A` from the home side's point of view; an
empty bin has `n: 0` and both summary values `null`. The lists are ordered by
the producer: models in the order above, metrics by season and then `all` before
`common`, calibration by model, outcome and bin index.

## Ingestion runs

A run is the record of one execution of one source loader over one season. It is
written once and only updated when the loader records its finish, so both
endpoints below read.

### `GET /v1/ingestion-runs`

One page of recorded runs.

| Parameter | Type | Default | Rule |
| --- | --- | --- | --- |
| `source` | `football_data` \| `openfootball` | none | Any other value is a `422`. |
| `season` | string | none (all seasons) | Season code. An unknown code is a `404`. |
| `status` | `running` \| `succeeded` \| `failed` | none | Any other value is a `422`. |
| `limit` | integer | `50` | `1`–`200`. |
| `offset` | integer | `0` | `>= 0`. |

Every filter is optional and the ones given are combined with `AND`. The order
is `started_at` descending, with the identifier descending as tie-break, so two
runs that started in the same instant still have one order and paging the same
filter twice cannot move a row between pages. `total` is the number of runs the
filter matched in the whole table, not the size of `items`.

The season filter is probed against the stored seasons first, so a code no
season has is a `404` and not a page that merely came back empty: the run table
keeps the code as plain text, and the answer would otherwise depend on whether
some run happens to carry that code.

```json
{
  "items": [
    {
      "id": "9c2e5f7a-4b18-4d6b-8c31-0a7f2e9d5b64",
      "source": "football_data",
      "season_code": "2025-26",
      "loader_version": "1",
      "started_at": "2026-11-16T06:00:02.118744Z",
      "finished_at": "2026-11-16T06:00:41.902117Z",
      "status": "succeeded",
      "rows_read": 380,
      "rows_upserted": 380,
      "rows_removed": 0,
      "rows_withdrawn": 0,
      "rows_rejected": 2,
      "content_sha256": "b41d2c8f6a0e5d97",
      "error": null
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

`finished_at` is `null` exactly while `status` is `running`; `content_sha256`
and `error` are `null` when the loader recorded none. Every counter is a number
of rows, and a zero means none were counted, not that the count is unknown.

### `GET /v1/ingestion-runs/{run_id}`

One recorded run with the source rows its loader refused.

| Parameter | Type | Rule |
| --- | --- | --- |
| `run_id` | UUID (path) | An unknown identifier is a `404`; a segment that is not a UUID is a `422`. |

The body is the collection shape plus `rejections_total` and `rejections`.
`rejections_total` counts every rejection the run recorded; `rejections` holds
the first 200 of them in `row_number` order, with the identifier as tie-break. A
client compares the two counts: when they are equal the embedded list is
complete, and when `rejections_total` is larger the response is capped.

```json
{
  "id": "9c2e5f7a-4b18-4d6b-8c31-0a7f2e9d5b64",
  "source": "openfootball",
  "season_code": "2025-26",
  "loader_version": "1",
  "started_at": "2026-11-16T06:00:44.003120Z",
  "finished_at": "2026-11-16T06:01:10.774901Z",
  "status": "succeeded",
  "rows_read": 380,
  "rows_upserted": 378,
  "rows_removed": 0,
  "rows_withdrawn": 0,
  "rows_rejected": 512,
  "content_sha256": null,
  "error": null,
  "rejections_total": 512,
  "rejections": [
    {
      "row_number": 3,
      "reason": "kickoff date is not a date",
      "raw": { "date": "TBD", "team1": "Club Norte", "team2": "Club Sur" }
    }
  ]
}
```

`raw` is the stored JSON object of the refused row, so a client renders what the
loader saw without a second request.

## Simulations

A simulation run is a stored request: the API writes the row, a worker executes
it and writes one row per club, and every read endpoint answers from PostgreSQL.
Only the first endpoint below writes.

### `POST /v1/simulations`

Ask for a run of the season simulator.

| Field | Type | Default | Rule |
| --- | --- | --- | --- |
| `season` | string | the season that starts latest among the seasons with a match | Season code. An unknown code is a `404`. |
| `as_of_date` | date (`YYYY-MM-DD`) | the Madrid date | Last date whose played matches count as fixed results. Later than the Madrid date is a `422`. |
| `n_runs` | integer | `10000` | `1000`–`50000`. A value outside the range is a `422`. |
| `seed` | integer | `0` | `0`–`9223372036854775807`, the range of the stored column. A value outside it is a `422`. |

The answer is **`202`**, not `201`: the row is stored but nothing has been
simulated yet, and `status` is `queued` until a worker claims it. The `Location`
header names the resource to poll.

```http
HTTP/1.1 202 Accepted
Location: /v1/simulations/0f5b3a8e-2c47-4b1e-9f6a-1d9c8b7e5a21
Content-Type: application/json
```

```json
{
  "id": "0f5b3a8e-2c47-4b1e-9f6a-1d9c8b7e5a21",
  "season": "2025-26",
  "as_of_date": "2026-11-16",
  "n_runs": 10000,
  "seed": 0,
  "model": "dixon_coles",
  "model_version": null,
  "status": "queued",
  "kind": "live",
  "trigger": "api",
  "requested_at": "2026-11-16T09:12:44.512931Z",
  "started_at": null,
  "finished_at": null,
  "error": null,
  "n_fixed": null,
  "n_simulated": null,
  "last_complete_matchday": null,
  "qualification": { "ucl_positions": 4, "europe_positions": 6, "relegation_positions": 3 }
}
```

`kind` is `live` for the season in progress and `backfill` for a retroactive
cut; `trigger` says what asked for the run: `api`, `cli` or `schedule`.
`model_version`, `started_at`, `n_fixed`, `n_simulated` and
`last_complete_matchday` are `null` because the run has not executed yet, which
is not the same as having counted zero. `qualification` repeats the thresholds
stored with the run, so `p_ucl` and the rest of the projected table keep the
meaning they had when the run was requested.

**Which season by default.** Without `season`, the answer is the season that
starts latest among the seasons with at least one match, the season
`GET /v1/standings` opens on. A body that names no season while no season has a
match is a `404` with the detail `No season with matches`; a named code that no
season carries is a `404` with `Season not found`.

**An identical request does not create a second run.** When an active run
already holds the same season, as-of date, run count, seed and model, the
response is that run, with its own `id`, and the worker is told about it again
only when the row looks abandoned: `queued` for more than one minute, or
`running` with a start older than thirty minutes.

**When the queue refuses the message.** The row is committed before the message
is sent, so a broker failure leaves a row nobody will execute. It is then stored
as `failed` when no worker has claimed it yet, with the enqueue instant as its
start and finish and a short `error` text that carries no broker address, and
the request answers `503` with a `Problem` body: title `Service Unavailable`,
detail `Simulation enqueue failed`. The broker's own text stays in the server
log, because it can carry the connection URL. A row a worker has already
claimed is never marked `failed` by a failed enqueue: the request answers `202`
and describes the stored run, `running` or already `succeeded`. A client
recovers by requesting the run again: a `failed` run does not hold the key, so
the next identical request stores a new one.

### `GET /v1/simulations`

One page of stored runs.

| Parameter | Type | Default | Rule |
| --- | --- | --- | --- |
| `season` | string | the season that starts latest among the seasons with a run | Season code. An unknown code is a `404`. |
| `status` | `queued` \| `running` \| `succeeded` \| `failed` | none | Any other value is a `422`. |
| `kind` | `live` \| `backfill` | none | Any other value is a `422`. |
| `limit` | integer | `50` | `1`–`200`. |
| `offset` | integer | `0` | `>= 0`. |

Every filter is optional and the ones given are combined with `AND`. The order
is `requested_at` descending, with the identifier descending as tie-break, so a
page is stable when two runs were requested in the same instant. `total` counts
the whole filtered set.

An omitted season selects the season that starts latest among the seasons with
at least one run, so a client opens on a season somebody has actually
simulated. When no season has a run the answer is `200` with an empty page and
`total: 0`, not a `404`: the collection exists and is empty. A given code that
no season carries is still a `404`.

Items have the shape shown above for the create request.

### `GET /v1/simulations/latest`

The last forecast a season produced, with the table it projected.

| Parameter | Type | Default | Rule |
| --- | --- | --- | --- |
| `season` | string | the season that starts latest among the seasons with a run | An unknown code is a `404`. |

"Latest" is the finish instant descending, with the request instant and the
identifier as tie-breaks, so the answer is the last forecast the season produced
and not the last one somebody asked for: a run still `queued`, or one that
failed, is not an answer a client can draw. A season with no succeeded run is a
`404` with the detail `no succeeded simulation`, which is a state of the season
and not a table with no rows; so is a body that names no season while no season
has a run. The body is the detail shape below.

### `GET /v1/simulations/history`

One forecast point per as-of date of a season, ascending.

| Parameter | Type | Default | Rule |
| --- | --- | --- | --- |
| `season` | string | the season that starts latest among the seasons with a run | Season code. An unknown code is a `404`. |
| `team_id` | integer | none | Only that club in every point. An unknown identifier is a `404`. |

A date contributes the latest succeeded run of that date and nothing else, so a
day that was simulated twice yields its last answer rather than an average of
both. A date without a succeeded run contributes no point, so the series is a
record of stored forecasts and not a calendar. `kind` tells a live forecast from
a retroactive one, because a backfilled date answers "what would the season have
looked like then", not "what did we say then".

`team_id` narrows every point to one club and keeps the rest of the point, so a
client tracks how one club's projected season moved from matchday to matchday. A
date whose run does not mention the club keeps its point with an empty `teams`
list, so filtering never silently shortens the series.

A given code that no season carries is a `404`; so is an unknown `team_id`, and
so is a body that names no season while no season has a run (detail
`no simulation`). A stored season without a run answers `200` with
`points: []`.

```json
{
  "season": "2025-26",
  "points": [
    {
      "run_id": "c4a17e5d-8b02-4f39-a6d1-5e2b9c0f7a13",
      "as_of_date": "2026-11-15",
      "kind": "live",
      "last_complete_matchday": 13,
      "teams": [
        {
          "team": { "id": 6, "name": "Club Norte" },
          "p_title": 0.031,
          "p_ucl": 0.417,
          "p_europe": 0.688,
          "p_relegation": 0.004,
          "expected_points": 61.2,
          "expected_position": 5.8
        }
      ]
    }
  ]
}
```

`teams` is in projected table order, filtered to the caller's club when it asked
for one, and a club the run does not mention is absent from the point rather
than present with zero probabilities.

### `GET /v1/simulations/{run_id}`

One stored run with the table it projected.

| Parameter | Type | Rule |
| --- | --- | --- |
| `run_id` | UUID (path) | An unknown identifier is a `404`; a segment that is not a UUID is a `422`. |

`table` is empty unless `run.status` is `succeeded`: a queued or failed run wrote
no team rows, and an empty table is therefore never a claim that the league has
no clubs. The probability arrays are shortened in the example; in a real answer
`probabilities` covers every total from `min` up to the highest reached, and
`position_probabilities` has one entry per club of the season.

```json
{
  "run": {
    "id": "c4a17e5d-8b02-4f39-a6d1-5e2b9c0f7a13",
    "season": "2025-26",
    "as_of_date": "2026-11-15",
    "n_runs": 10000,
    "seed": 0,
    "model": "dixon_coles",
    "model_version": "1",
    "status": "succeeded",
    "kind": "live",
    "trigger": "api",
    "requested_at": "2026-11-15T09:40:07.221408Z",
    "started_at": "2026-11-15T09:40:07.446190Z",
    "finished_at": "2026-11-15T09:40:52.908311Z",
    "error": null,
    "n_fixed": 130,
    "n_simulated": 250,
    "last_complete_matchday": 13,
    "qualification": { "ucl_positions": 4, "europe_positions": 6, "relegation_positions": 3 }
  },
  "table": [
    {
      "team": { "id": 6, "name": "Club Norte" },
      "current": { "played": 13, "points": 25, "goal_difference": 7 },
      "expected_points": 61.2,
      "points_quantiles": { "p05": 48, "p25": 56, "p50": 61, "p75": 66, "p95": 74 },
      "points_distribution": { "min": 41, "probabilities": [0.0004, 0.0011, 0.0026] },
      "expected_position": 5.8,
      "position_probabilities": [0.001, 0.004, 0.012],
      "p_title": 0.031,
      "p_ucl": 0.417,
      "p_europe": 0.688,
      "p_relegation": 0.004
    }
  ]
}
```

### The projected table

The table is in projected order: mean final position ascending, then mean points
descending, then club name, then club identifier. The last two keys close the
order, so no two rows compare equal and the API prints the ranking the worker
prints. Each row carries:

- `current`: the real standing the run started from, computed from the played
  matches dated strictly before the as-of date with the same tie-break rules as
  `GET /v1/standings`.
- `expected_points` and `expected_position`: means over the simulated seasons, so
  both are fractional. `expected_position` is not the position of the row in
  this table.
- `points_quantiles`: totals of the final points, not offsets from
  `current.points`, so they are directly comparable with it.
- `points_distribution`: `min` is the lowest final total the run reached, and
  entry `i` of `probabilities` belongs to `min + i`; the array covers every total
  between its ends, so it has no gap, and its entries sum to one within
  floating-point tolerance.
- `position_probabilities`: one entry per club of the season, index 0 being first
  place, so its entries also sum to one.
- `p_title`, `p_ucl`, `p_europe` and `p_relegation`: the probability that the club
  finishes first, within `qualification.ucl_positions` places, within
  `qualification.europe_positions` places, or inside the last
  `qualification.relegation_positions` places.

### Run states and polling

| Status | Meaning | Timestamps |
| --- | --- | --- |
| `queued` | The row is stored and holds its key; no worker has claimed it. | `started_at` is `null`. |
| `running` | A worker claimed the run and is executing it. | `started_at` is set, `finished_at` is `null`. |
| `succeeded` | The run finished and wrote its team rows. | Both are set. |
| `failed` | The run terminated without a table. | Both are set, `error` carries a short text. |

A client posts once, reads the `Location` header of the `202` and polls
`GET /v1/simulations/{run_id}` until `status` is `succeeded` or `failed`; the
table arrives with the terminal state and is empty before it. A `failed` run is
terminal too: nothing retries it, so the client asks again with a new request.
The worker executes one run at a time, so a run queued behind another waits
without any change to its own row.

## Jobs and schedule

The API enqueues one job per run on the Celery queue `lmi`, as the task
`lmi.simulate` carrying the run identifier, and the worker consumes that queue.
There is no result backend: the row in PostgreSQL is the only record of what a
run did, which is why every read endpoint above answers without asking the
broker and why a lost message leaves a row that can be sent again. Ingestion is
never a queued job: it downloads source files and stays an explicit command.

A daily cycle runs at 06:00 Europe/Madrid: it ingests the season in progress,
stores that day's live predictions and simulates the league, storing the run
with `trigger: "schedule"`. It runs in Celery beat, which is the Compose profile
`scheduler` and off by default, because its ingestion step needs the network and
the default stack stays offline.

**What the schedule is for.** The batch of day `D` covers only the fixtures
strictly after `D`, so a match dated `D` is last predicted on `D-1`. The daily
run is what keeps that last forecast, and a skipped day leaves the matches of
that day with the previous day's forecast and none from the day itself. The
history of a match therefore has gaps on the days the cycle did not run, and
nothing backfills them.

**Stored predictions are immutable.** The table holds one row per match, model
and as-of date, and triggers enforce it: an update or a delete of a stored
prediction, and a truncation of the table, are rejected by the database, while
the insert trigger stamps `created_at` and refuses a row whose as-of date is not
the Madrid date of the insert, whose match is not `scheduled`, or whose creation
falls at or after the kickoff date boundary. A forecast is therefore a
historical record of what the system said before a match, not a value that
improves with time: a later run of the same date keeps every row already stored
and adds the ones it does not have, so the earlier dates stay untouched.

