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
| `detail` | A sentence written by the API, never a stack trace or a SQL fragment. The matches, standings, predictions, model-metrics, ingestion-runs and simulations routes use a fixed sentence that never repeats a value the client sent, and so do the seasons and teams routes. |
| `errors` | Present only on a validation failure: one entry per rejected value with `loc`, `msg` and `type`. The rejected value itself is never echoed. |

The statuses a route can return are declared in its OpenAPI responses, so the
generated client models the real body instead of a default error envelope:

| Route | `404` | `422` | `503` |
| --- | --- | --- | --- |
| `GET /v1/seasons` | — | — | — |
| `GET /v1/teams` | unknown season code | declared, not reachable (its only parameter is an unconstrained string) | — |
| `GET /v1/teams/{team_id}` | unknown team | non-integer identifier | — |
| `GET /v1/teams/{team_id}/season-stats` | unknown team, unknown season code, a team that is not part of the season, or no season with matches | non-integer identifier | — |
| `GET /v1/teams/{team_id}/focus` | unknown team, unknown season code, a team that is not part of the season, or no season with matches | non-integer identifier | — |
| `GET /v1/teams/{team_id}/history` | unknown team | non-integer identifier | — |
| `GET /v1/teams/{team_id}/relegation-race` | unknown team, unknown season code, a team that is not part of the season, or no completed season for the survival threshold | non-integer identifier | — |
| `GET /v1/predictions` | unknown season code, or an unknown `match_id` or `team_id` | a `matchday`, `model`, `match_status`, `evaluation` or date value outside its rule | — |
| `GET /v1/predictions/{prediction_id}` | unknown prediction | non-integer identifier | — |
| `GET /v1/matches` | unknown season code or team identifier | a query value outside its rule | — |
| `GET /v1/matches/{match_id}` | unknown match | non-integer identifier | — |
| `GET /v1/matches/{match_id}/preview` | unknown match | non-integer identifier | — |
| `GET /v1/standings` | unknown season code, or no season with matches | malformed `as_of` | — |
| `GET /v1/model-metrics` | `no backtest run`: no succeeded run is stored, or `run_id` names one that is missing, running or failed | malformed `run_id` | — |
| `GET /v1/model-metrics/live` | unknown season code, or no season with matches | declared, not reachable (its only parameter is an unconstrained string) | — |
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

### `GET /v1/teams/{team_id}/season-stats`

One team's season as per-match averages, venue and opponent-tier records, and
one row per played match.

| Parameter | Type | Default | Rule |
| --- | --- | --- | --- |
| `season` | string | the current season (see below) | Season code. An unknown code is a `404`. |

**Which season by default.** The same rule the standings endpoint applies:
without `season`, the answer is the greatest `start_year` among the seasons that
have at least one match. When no season has any match the response is a `404`.

**Which team.** The club has to be a side of at least one match of that season,
because the season's team set is the one the table ranks. A club that only plays
another season is a `404` with the detail `Team not found in season`, which is a
different answer from the `Team not found` of an identifier no club has.

**Which matches are counted.** The played matches of the season the club was a
side of. A `scheduled` or `withdrawn` fixture contributes nothing, so `played`,
`points` and every counter below describe results only. `played` and `points`
are the club's row of the season table, so this endpoint can never report a
total the standings endpoint contradicts.

**The average lines.** `per_match.for` is the club's own line, `per_match.against`
the line of the clubs it faced, and `league_avg` the same line over both sides of
every played match of the season, so the three are compared field by field.
Every number is a per-match average, and each field is averaged over only the
matches that carry it: a match without a statistics row counts in `goals` and in
nothing else, and a statistics row without expected goals counts in every field
but `xg`. A field no counted match carries is `null`, never `0.0`; a field every
counted match records as zero averages to `0.0`.

**Expected points.** `xpts` of one match is the points the pre-closing market
price implies, `3 · p_win + p_draw` from the margin-removed `1x2`
probabilities, seen from the club's venue. The price is the one the live loader
selects: the most trusted bookmaker (`pinnacle`, then `market_avg`, then
`bet365`) among the **pre-closing** rows. A closing price is never read, because
it is only known at kickoff. `xpts_market` sums the expected points of the
matches that carry a price and is `null` when none does; it is a sum, not an
average, because the points of matches add up.

**The coverage counters.** `xpts_coverage` is the number of counted matches that
carry a pre-closing price and therefore contribute to `xpts_market`;
`stat_coverage` is the number that carry a statistics row; `xg_coverage` is the
number that carry an expected-goals value on either side. A client divides the
sum by `xpts_coverage` to read an average, and compares the coverage with
`played` to see how much of the season rests on data the source published.

**Splits.** `splits.home` and `splits.away` count the same played matches,
partitioned by venue. The two records add up to the club's table row.

**Tiers.** `tiers` splits the same matches by the opponent's position in the
season table: with `n` teams the **top** tier is positions `1..ceil(n/3)`, the
**middle** the next `ceil(n/3)` and the **bottom** the rest. Each record carries
the counters of every match against a club of that tier, whatever the venue, plus
`team_ids`, the ascending identifiers of the clubs that occupy those positions.
The membership is the tier, not only the opponents the club has already met. A
season whose table holds fewer than five clubs can leave the bottom tier empty
(with `n = 4`, `ceil(n/3) = 2` fills the top and the middle); an empty tier is
served as a record of zeros with a `null` `points_per_game` and an empty
`team_ids`, never dropped.

**The match rows.** `matches` is chronological: kickoff date ascending, then
match identifier. Each row nests the fixture summary, the venue, the points,
`xpts`, the venue-scoped goals, the shots of both sides, and `rolling5`, the
averages over the at most five matches the club played on a date **strictly
earlier** than that row's date. The window never includes the match it
describes and never another fixture of the same day: a stored date cannot order
two fixtures of one day, so the form of a match is the form before its date,
the same strict reading the match preview applies. A row with no earlier match
(the club's first fixture, or both fixtures of its first day) therefore has an
all-`null` `rolling5`. `points_pg`, `goals_for_pg` and `goals_against_pg` are
`null` only when the window is empty; `xpts_pg` is also `null` when no window
match carried a price.

One row of `matches` is shown; a season of 38 matchdays returns 38.

```json
{
  "team": { "id": 5, "name": "Barcelona" },
  "season": "2024-25",
  "played": 38,
  "points": 88,
  "xpts_market": 74.31,
  "xpts_coverage": 36,
  "per_match": {
    "for": {
      "goals": 2.68,
      "shots": 15.1,
      "shots_on_target": 5.6,
      "fouls": 10.4,
      "corners": 6.2,
      "yellow": 1.6,
      "red": 0.05,
      "xg": 1.87
    },
    "against": {
      "goals": 1.03,
      "shots": 9.4,
      "shots_on_target": 3.5,
      "fouls": 13.8,
      "corners": 4.1,
      "yellow": 2.4,
      "red": 0.13,
      "xg": 1.04
    }
  },
  "league_avg": {
    "goals": 1.27,
    "shots": 12.05,
    "shots_on_target": 4.24,
    "fouls": 12.46,
    "corners": 4.86,
    "yellow": 2.12,
    "red": 0.08,
    "xg": 1.29
  },
  "stat_coverage": 38,
  "xg_coverage": 38,
  "splits": {
    "home": {
      "played": 19,
      "won": 15,
      "drawn": 2,
      "lost": 2,
      "goals_for": 55,
      "goals_against": 18,
      "points": 47,
      "points_per_game": 2.47
    },
    "away": {
      "played": 19,
      "won": 13,
      "drawn": 2,
      "lost": 4,
      "goals_for": 47,
      "goals_against": 21,
      "points": 41,
      "points_per_game": 2.16
    }
  },
  "tiers": {
    "top": {
      "played": 12,
      "won": 9,
      "drawn": 1,
      "lost": 2,
      "goals_for": 30,
      "goals_against": 14,
      "points": 28,
      "points_per_game": 2.33,
      "team_ids": [1, 3, 4, 5, 7, 9, 11]
    },
    "middle": {
      "played": 12,
      "won": 10,
      "drawn": 2,
      "lost": 0,
      "goals_for": 34,
      "goals_against": 9,
      "points": 32,
      "points_per_game": 2.67,
      "team_ids": [2, 6, 8, 10, 12, 13, 14]
    },
    "bottom": {
      "played": 14,
      "won": 9,
      "drawn": 1,
      "lost": 4,
      "goals_for": 38,
      "goals_against": 16,
      "points": 28,
      "points_per_game": 2.0,
      "team_ids": [15, 16, 17, 18, 19, 20]
    }
  },
  "matches": [
    {
      "match": {
        "id": 4182,
        "season": "2024-25",
        "kickoff_date": "2024-08-17",
        "kickoff_time": "21:00:00",
        "matchday": 1,
        "status": "played",
        "home_team": { "id": 5, "name": "Barcelona" },
        "away_team": { "id": 12, "name": "Valencia" },
        "home_goals": 2,
        "away_goals": 1,
        "ht_home_goals": 1,
        "ht_away_goals": 1,
        "result": "H"
      },
      "venue": "home",
      "points": 3,
      "xpts": 2.21,
      "goals_for": 2,
      "goals_against": 1,
      "shots_for": 17,
      "shots_against": 8,
      "rolling5": {
        "points_pg": null,
        "xpts_pg": null,
        "goals_for_pg": null,
        "goals_against_pg": null
      }
    }
  ]
}
```

### `GET /v1/teams/{team_id}/focus`

One club's season as a single read: its table row, its recent form, its venue
split, the fixtures still to come with the forecasts stored for them, and its
outlook in the latest succeeded simulation.

| Parameter | Type | Default | Rule |
| --- | --- | --- | --- |
| `season` | string | the current season (see below) | Season code. An unknown code is a `404`. |

**Which season by default.** The same rule the standings endpoint applies:
without `season`, the answer is the greatest `start_year` among the seasons that
have at least one match. When no season has a match the response is a `404` with
the detail `No season with matches`, and a code no season carries is a `404`
with the detail `Season not found`.

**Which club.** The club has to be a side of at least one match of that season,
whatever the status of that match, because the season's team set is the one the
table ranks. A club that only plays another season is a `404` with the detail
`Team not found in the season`; an identifier no club has is a `404` with the
detail `Team not found`. A `team_id` that is not an integer is a `422` whose
`detail` is `Request validation failed`. None of those sentences repeats the
value the client sent.

**The table row.** `standing` is the team's row of the season table: the nested
`team` with its `id` and `name`, then `position`, `played`, `won`, `drawn`,
`lost`, `goals_for`, `goals_against`, `goal_difference`, `points` and a `form`
of the last five outcomes of the season. The field is nullable in the schema
because the payload is also the shape a read over a partial table would return,
but the team-set rule above makes it non-null for every team this endpoint
serves: a null there means a broken read, not missing data.

**The form and the splits.** `form` covers the last five played matches of the
season, newest first. It carries the `team` the letters are seen from, `results`
with one letter per match, `points`, `goals_for` and `goals_against` over
exactly those matches, and `matches`, the same fixtures in the same order.
`splits` covers every played match of the season by venue: `home` and `away`
each carry `played`, `won`, `drawn`, `lost`, `goals_for`, `goals_against`,
`points` and `points_per_game`, which is null for a side that played no match.
The two records differ from `form` in scope, not in arithmetic.

**The fixtures still to come.** `upcoming` lists the next five fixtures that are
`scheduled` and dated on or after the Madrid date of the request, in
kickoff-date then identifier order. Each entry nests `match` and `predictions`.
The fixture is the summary the matches collection returns: `id`, `season`,
`kickoff_date`, `kickoff_time`, `matchday`, `status`, `home_team`, `away_team`,
`home_goals`, `away_goals`, `ht_home_goals`, `ht_away_goals` and `result`, where
the goal and result fields are null while the fixture is still `scheduled`. Each
prediction carries `prediction_id`, `model`, `model_version`, `created_at`,
`as_of_date`, `probabilities` (of `home`, `draw` and `away`) and `evaluation`,
which is the outcome the core scorer gives the stored row. `predictions` holds
the standing row of each model: the newest row that can still stand, which for a
fixture still to be played is the row the scorer calls `pending`, with the
fallback to the newest `late` row when the model has no such row for the
fixture, so a fixture whose date moved earlier than its rows still reports what
was stored for it. The list is ordered by the report position of the model and
then by its version. The five fixtures are read in one statement and evaluated
once, so the number of queries does not grow with the number of fixtures, and a
fixture with no stored forecast still appears with an empty `predictions` list.

**The outlook.** `outlook` is the team's row of the latest succeeded simulation
run of the season, the run chosen by `finished_at`, which is not the run with
the latest `as_of_date`. Every field is a stored column of that run, so nothing
is re-simulated: `run_id` is the trace back to the full table of the run, which
`GET /v1/simulations/{run_id}` serves, and `as_of_date` is the cut the run
simulated from. `expected_points` and `expected_position` are means over the
simulated seasons and are therefore fractional; they are the stored
`mean_points` and `mean_position` of the run. `p_title`, `p_ucl`, `p_europe` and
`p_relegation` are the probabilities the projected table of a simulation
defines: the club finishes first, inside the UCL places, inside the European
places, or inside the relegation places, as
[the projected table](#the-projected-table) states them. `points_p05` and
`points_p95` are the 5th and the 95th percentile of the simulated final points
total, which is why they are whole points while the two expected values are not.
The field is null when the season has no succeeded run, or when that run stored
no row for the team.

**The derived values.** Every other value is derived on read, through the same
core functions the standings, predictions and live-metrics endpoints use, so
this view cannot disagree with them.

One of the five matches behind `form`, one of the five fixtures of `upcoming`
and one of its forecasts are shown.

```json
{
  "team": { "id": 5, "name": "Barcelona" },
  "season": "2026-27",
  "standing": {
    "position": 1,
    "team": { "id": 5, "name": "Barcelona" },
    "played": 13,
    "won": 10,
    "drawn": 2,
    "lost": 1,
    "goals_for": 34,
    "goals_against": 12,
    "goal_difference": 22,
    "points": 32,
    "form": ["W", "W", "L", "W", "W"]
  },
  "form": {
    "team": { "id": 5, "name": "Barcelona" },
    "results": ["W", "W", "L", "W", "W"],
    "points": 12,
    "goals_for": 13,
    "goals_against": 5,
    "matches": [
      {
        "id": 4290,
        "season": "2026-27",
        "kickoff_date": "2026-11-08",
        "kickoff_time": "18:30:00",
        "matchday": 12,
        "status": "played",
        "home_team": { "id": 5, "name": "Barcelona" },
        "away_team": { "id": 8, "name": "Real Betis" },
        "home_goals": 4,
        "away_goals": 1,
        "ht_home_goals": 2,
        "ht_away_goals": 0,
        "result": "H"
      }
    ]
  },
  "splits": {
    "home": {
      "played": 7,
      "won": 6,
      "drawn": 1,
      "lost": 0,
      "goals_for": 21,
      "goals_against": 5,
      "points": 19,
      "points_per_game": 2.71
    },
    "away": {
      "played": 6,
      "won": 4,
      "drawn": 1,
      "lost": 1,
      "goals_for": 13,
      "goals_against": 7,
      "points": 13,
      "points_per_game": 2.17
    }
  },
  "upcoming": [
    {
      "match": {
        "id": 4311,
        "season": "2026-27",
        "kickoff_date": "2026-11-22",
        "kickoff_time": "21:00:00",
        "matchday": 14,
        "status": "scheduled",
        "home_team": { "id": 12, "name": "Valencia" },
        "away_team": { "id": 5, "name": "Barcelona" },
        "home_goals": null,
        "away_goals": null,
        "ht_home_goals": null,
        "ht_away_goals": null,
        "result": null
      },
      "predictions": [
        {
          "prediction_id": 9812,
          "model": "dixon_coles",
          "model_version": "1",
          "created_at": "2026-11-16T06:00:12.418003Z",
          "as_of_date": "2026-11-16",
          "probabilities": { "home": 0.2814, "draw": 0.2602, "away": 0.4584 },
          "evaluation": "pending"
        }
      ]
    }
  ],
  "outlook": {
    "run_id": "e76dd01f-21e0-46c1-ad86-404bc30a983e",
    "as_of_date": "2026-11-16",
    "p_title": 0.412,
    "p_ucl": 0.874,
    "p_europe": 0.951,
    "p_relegation": 0.0,
    "expected_points": 78.4,
    "points_p05": 66,
    "points_p95": 91,
    "expected_position": 1.9
  }
}
```

### `GET /v1/teams/{team_id}/history`

One club's seasons, ascending, with its table position, its per-game averages and
the steepest decline of each metric between two adjacent seasons.

The resource takes no query parameter: it is the club's whole stored history,
so a caller that wants one season asks
[the season statistics](#get-v1teamsteam_idseason-stats) instead.

**Which club.** An identifier no stored club has is a `404` with the detail
`Team not found`. A stored club that has played no match at all answers `200`
with empty `seasons` and `largest_drop`: a club without history is a state of
the data, not an error, and a line of zeros would invent a season it never
played.

**Which seasons are listed.** Exactly the seasons in which the club played at
least one match, meaning a match with status `played` and both goal columns set.
A season in which the club only had fixtures, or none at all, is absent rather
than served with zeros. The order is the season `start_year` ascending and then
the season code, so two reads of one history serve the same list.

**The position.** `position` is the club's place in that season's table as the
[standings endpoint](#get-v1standings) ranks it: the team set is every club that
appears as a side of any match of the season, and the counted results are the
played ones. Both endpoints rank a season with the same function, so over the same results they place a club the same way, and a club the table does not rank keeps a `null` instead of a borrowed place. A standings request that passes `as_of` counts only the matches up to that date, which is a shorter season than this resource reports, so that table can rank the club differently on purpose. Two requests are still two reads of a stored state that can
move between them, and a club that changes place after a result is stored is a
change of the data rather than a disagreement about the rule.

**The averages.** `points_pg`, `goals_for_pg` and `goals_against_pg` divide by
`played` and are always known for a listed season. Every other average is taken
over only the matches that carry the value, so its denominator can be smaller
than `played` and can differ from field to field: a match without a statistics
row counts in the goals and in nothing else, and a statistics row without
cards counts in the shots and in nothing else. A field no counted match carries
is `null`, never `0.0`, while a field every counted match records as zero
averages to `0.0`. `cards_pg` averages the club's own yellow plus red per
match, not the difference between the two sides. `xpts_pg` averages the expected
points of the fixtures that carry an eligible **pre-closing** `1x2` price, by
the same selection and the same formula the season statistics apply, so a
closing price is ignored whatever its bookmaker.

**The largest drops.** `largest_drop` answers which numbers fell between two
adjacent seasons of the list. It covers the nine per-game metrics of the payload
-- `points_pg`, `goals_for_pg`, `goals_against_pg`, `shots_for_pg`,
`shots_against_pg`, `sot_for_pg`, `sot_against_pg`, `cards_pg` and `xpts_pg` --
and names each one by its payload key, so an entry can be matched to the number
it describes. Absolute `points` is deliberately not a metric: a season can be
shorter, and a drop in a total would then describe the calendar instead of the
club.

`delta` is oriented so that a **negative** value always means the club got worse:
`later - earlier` for the five higher-is-better metrics, and `earlier - later`
for `goals_against_pg`, `shots_against_pg` and `cards_pg`, so conceding more and
collecting more cards read as a fall like any other number that got worse. A
pair with a `null` on either side is skipped for that metric alone, because a gap
in the source is not a decline on the pitch. Each metric contributes at most one
entry -- its steepest decline -- so a metric that never fell is absent, and the
list is empty when nothing declined and when fewer than two seasons are listed.
The order is `delta` ascending and then `metric`.

Two of the four seasons are shown.

```json
{
  "team": { "id": 1, "name": "Alavés" },
  "seasons": [
    {
      "season": "2007-08",
      "position": 14,
      "played": 38,
      "points": 47,
      "points_pg": 1.236842105263158,
      "goals_for_pg": 1.5,
      "goals_against_pg": 1.605263157894737,
      "shots_for_pg": null,
      "shots_against_pg": null,
      "sot_for_pg": null,
      "sot_against_pg": null,
      "cards_pg": null,
      "xpts_pg": null
    },
    {
      "season": "2030-31",
      "position": 20,
      "played": 10,
      "points": 6,
      "points_pg": 0.6,
      "goals_for_pg": 1.4,
      "goals_against_pg": 2.3,
      "shots_for_pg": 10.0,
      "shots_against_pg": 10.0,
      "sot_for_pg": 4.0,
      "sot_against_pg": 4.0,
      "cards_pg": 2.5,
      "xpts_pg": 1.3596881959910916
    }
  ],
  "largest_drop": [
    {
      "metric": "goals_against_pg",
      "from_season": "2007-08",
      "to_season": "2030-31",
      "delta": -0.6947368421052629
    },
    {
      "metric": "points_pg",
      "from_season": "2007-08",
      "to_season": "2030-31",
      "delta": -0.636842105263158
    }
  ]
}
```

### `GET /v1/teams/{team_id}/relegation-race`

One club's survival race in one season: the league's historical survival line,
the club and the clubs it is racing, their run history and the fixtures still to
play.

| Parameter | Type | Default | Rule |
| --- | --- | --- | --- |
| `season` | string | the current season (see below) | Season code. An unknown code is a `404`. |

**Which season by default.** The same rule the standings endpoint applies:
without `season`, the answer is the greatest `start_year` among the seasons that
have at least one match, and a database in which no season has a match is a
`404`.

**Which club.** An identifier no stored club has is a `404` with the detail
`Team not found`. A stored club that is not a side of any match of the resolved
season is a `404` with the detail `Team not found in season`, which is a
different answer from an identifier no club has.

**The survival threshold.** The line answers how many points usually keep a club
up. It is read from the final table of every **completed 20-club season from
2005-06 onwards**, and the counted place is the highest relegated one: with the
configured three relegation places that is position 17, which is what `position`
reports. A season is completed only when every fixture of its double round
robin was played, so the 380 results of twenty clubs are the proof; a season
still being played is skipped instead of contributing a partial table, and no
clock is read anywhere. `points_median` is the median of that place's final
totals and is never rounded, `points_min` and `points_max` are the extremes of
the same sample, and `seasons_used` is its size, so a client can see how thin
the evidence is. The sample does not depend on the `season` asked for: a request
for an old season also counts the completed seasons stored after it, because the
line is a historical reference, not a forecast made at that time. When no stored season qualifies the endpoint is a `404` with
the detail `no completed season`: the field is not nullable, and falling back to
the current table or serving zero would read as a survival line somebody
computed.

**The rivals and the series.** `rivals` is the club plus the four clubs nearest
to it by expected final position in the latest **succeeded** simulation of the
season, served in expected-position order. "Latest" is the finish instant
descending, then the as-of date, then the identifier, so the race and the team
focus describe the same run. A queued, running or failed run measures nothing
and is never read. Distance is absolute, so the neighbours may sit above or
below the club, and a tie is decided by the lower expected position and then by
the lower identifier. Each rival carries the stored columns of that one run:
the real points at the as-of date, the mean final total, the relegation
probability and the 5th and 95th percentiles of the projected total.

`series` is the [simulation history](#get-v1simulationshistory) of the season
restricted to exactly those clubs, one point per as-of date from the latest
succeeded run of that date, so the group's forecast can be charted without a
second request. A stored season without a succeeded run answers `200` with
`run_id` and `as_of_date` `null`, `rivals: []` and a series that names the
season and holds no point, while the threshold and the fixtures are served as
usual: a race without a simulation is a state of the data and not an error. The
same run that holds no row for the club gives `rivals: []` and still names the
run.

**The remaining fixtures.** `remaining_fixtures` is the club's `scheduled`
matches of the season, ascending by kickoff date and then identifier, each with
the fixture summary, the venue the club plays it at, and the opponent's position
in the season's table. A `withdrawn` fixture is not listed: it will not be
played. `opponent_position` is `null` for an opponent the table does not rank.

**The per-fixture and next-match projections.** `remaining_fixtures[].p_win`,
`p_draw` and `p_loss` are the latest succeeded run's stored probabilities of the
three results, always from the club's side: at home they are the stored
home/draw/away triple and away the same triple read from the other end, so
`p_win` means the club wins wherever it plays. `next_match` describes the fixture
the club plays next: `match_id` is the first of `remaining_fixtures` dated on or after
today in Madrid (the same match the focus page lists first as upcoming; a postponed
fixture dated earlier stays in `remaining_fixtures` but is not next), and
`outcomes.win`, `outcomes.draw` and `outcomes.loss` each carry `p_outcome` (the
share of the simulated runs that produced that result), the conditional
`p_relegation`, `p_europe` and `p_title` (the shares of those same runs that
ended each way), and `expected_points` (their mean final total).
`next_match.current` is the club's own row of the same run: `p_relegation`,
`p_europe`, `p_title` and `expected_points` for the whole remaining season, so a
client computes every delta of the block against one baseline instead of mixing
two runs. For each of relegation, Europe and the title, `sum over outcomes of
(p_outcome * p_<field>)` equals the matching `next_match.current` field within
the precision shown, and the three `p_outcome` sum to one.

**When the projections are null.** Both groups stay `null` where the run did not
store them, and the response is still `200`. A run created before revision 0004
has no per-fixture or next-match detail, so the fixture triple and `next_match`
are `null`; a fixture the run did not simulate keeps three `null`
probabilities; and `next_match` is `null` when the stored match is no longer the
club's next fixture (a club whose next match was played on the as-of day gets
`null` until the next run replaces it), when a result was never sampled
(`n_outcome` zero, so the conditional probabilities have no denominator), and
when the run holds no row for the club. A `null` is unknown, never zero.

**Two measurements, not a contradiction.** `p_outcome` is the simulated
frequency of a result, not a model probability, so it may differ slightly --
about half a percentage point at 10 000 runs -- from the model probability in
the `p_win`, `p_draw` and `p_loss` of the `remaining_fixtures` entry whose match id is
`next_match.match_id`. They
are two measurements of the same match, not a contradiction: the frequency is
what the run's samples produced, while the fixture triple is the model's
probability of the fixture.

One rival, one series point and one fixture are shown.

```json
{
  "team": { "id": 1, "name": "Alavés" },
  "season": "2030-31",
  "run_id": "e76dd01f-21e0-46c1-ad86-404bc30a983e",
  "as_of_date": "2030-10-26",
  "survival_threshold": {
    "points_median": 45.0,
    "points_min": 43,
    "points_max": 47,
    "seasons_used": 3,
    "position": 17
  },
  "rivals": [
    {
      "team": { "id": 18, "name": "Hércules" },
      "current_points": 9,
      "expected_points": 41.994,
      "p_relegation": 0.279,
      "points_p05": 31,
      "points_p95": 53
    }
  ],
  "series": {
    "season": "2030-31",
    "points": [
      {
        "run_id": "e76dd01f-21e0-46c1-ad86-404bc30a983e",
        "as_of_date": "2030-10-26",
        "kind": "live",
        "last_complete_matchday": 10,
        "teams": [
          {
            "team": { "id": 18, "name": "Hércules" },
            "p_title": 0.0,
            "p_ucl": 0.002,
            "p_europe": 0.013,
            "p_relegation": 0.279,
            "expected_points": 41.994,
            "expected_position": 15.415
          }
        ]
      }
    ]
  },
  "remaining_fixtures": [
    {
      "match": {
        "id": 1241,
        "season": "2030-31",
        "kickoff_date": "2030-10-26",
        "kickoff_time": null,
        "matchday": 11,
        "status": "scheduled",
        "home_team": { "id": 1, "name": "Alavés" },
        "away_team": { "id": 10, "name": "Eibar" },
        "home_goals": null,
        "away_goals": null,
        "ht_home_goals": null,
        "ht_away_goals": null,
        "result": null
      },
      "venue": "home",
      "opponent_position": 16,
      "p_win": 0.455,
      "p_draw": 0.275,
      "p_loss": 0.270
    }
  ],
  "next_match": {
    "match_id": 1241,
    "outcomes": {
      "win": {
        "p_outcome": 0.46,
        "p_relegation": 0.08,
        "p_europe": 0.12,
        "p_title": 0.002,
        "expected_points": 44.2
      },
      "draw": {
        "p_outcome": 0.27,
        "p_relegation": 0.34,
        "p_europe": 0.02,
        "p_title": 0.0,
        "expected_points": 41.0
      },
      "loss": {
        "p_outcome": 0.27,
        "p_relegation": 0.58,
        "p_europe": 0.0,
        "p_title": 0.0,
        "expected_points": 37.8
      }
    },
    "current": {
      "p_relegation": 0.285,
      "p_europe": 0.061,
      "p_title": 0.001,
      "expected_points": 41.608
    }
  }
}
```

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

### `GET /v1/matches/{match_id}/preview`

The pre-kickoff view of one fixture: the match itself, the form of both sides as
it stood before it, the record of the pair, the stored forecasts of the fixture
and the market price.

The resource takes no query parameter: one fixture is one preview, and every
value below is a stored row or is derived from one. One meeting of
`head_to_head`, one fixture of each form summary and one forecast are shown.

```json
{
  "match": {
    "id": 7981,
    "season": "2025-26",
    "kickoff_date": "2026-05-10",
    "kickoff_time": "21:00:00",
    "matchday": 37,
    "status": "played",
    "home_team": { "id": 12, "name": "Valencia" },
    "away_team": { "id": 5, "name": "Barcelona" },
    "home_goals": 1,
    "away_goals": 2,
    "ht_home_goals": 0,
    "ht_away_goals": 1,
    "result": "A"
  },
  "home_form": {
    "team": { "id": 12, "name": "Valencia" },
    "results": ["D", "W", "L", "L", "W"],
    "points": 7,
    "goals_for": 6,
    "goals_against": 5,
    "matches": [
      {
        "id": 7975,
        "season": "2025-26",
        "kickoff_date": "2026-05-03",
        "kickoff_time": "18:30:00",
        "matchday": 36,
        "status": "played",
        "home_team": { "id": 12, "name": "Valencia" },
        "away_team": { "id": 10, "name": "Eibar" },
        "home_goals": 1,
        "away_goals": 1,
        "ht_home_goals": 1,
        "ht_away_goals": 0,
        "result": "D"
      }
    ]
  },
  "away_form": {
    "team": { "id": 5, "name": "Barcelona" },
    "results": ["W", "W", "W", "D", "W"],
    "points": 13,
    "goals_for": 12,
    "goals_against": 4,
    "matches": [
      {
        "id": 7976,
        "season": "2025-26",
        "kickoff_date": "2026-05-03",
        "kickoff_time": "21:00:00",
        "matchday": 36,
        "status": "played",
        "home_team": { "id": 5, "name": "Barcelona" },
        "away_team": { "id": 8, "name": "Real Betis" },
        "home_goals": 3,
        "away_goals": 0,
        "ht_home_goals": 2,
        "ht_away_goals": 0,
        "result": "H"
      }
    ]
  },
  "head_to_head": {
    "played": 14,
    "home_side_wins": 4,
    "draws": 3,
    "away_side_wins": 7,
    "matches": [
      {
        "id": 7702,
        "season": "2025-26",
        "kickoff_date": "2025-12-14",
        "kickoff_time": "21:00:00",
        "matchday": 16,
        "status": "played",
        "home_team": { "id": 5, "name": "Barcelona" },
        "away_team": { "id": 12, "name": "Valencia" },
        "home_goals": 3,
        "away_goals": 1,
        "ht_home_goals": 1,
        "ht_away_goals": 1,
        "result": "H"
      }
    ]
  },
  "predictions": [
    {
      "prediction_id": 9104,
      "model": "elo",
      "model_version": "1",
      "created_at": "2026-05-09T06:00:07.113422Z",
      "as_of_date": "2026-05-09",
      "probabilities": { "home": 0.31, "draw": 0.28, "away": 0.41 },
      "evaluation": "scored"
    }
  ],
  "odds": {
    "bookmaker": "market_avg",
    "is_closing": false,
    "home": 3.54,
    "draw": 3.98,
    "away": 1.84,
    "implied": { "home": 0.262236, "draw": 0.233246, "away": 0.504518 }
  }
}
```

**The fixture.** `match` is the fixture in the summary shape the matches
collection returns: `id`, `season`, `kickoff_date`, `kickoff_time`, `matchday`,
`status`, `home_team` and `away_team` (each a club with its `id` and `name`),
`home_goals`, `away_goals`, `ht_home_goals`, `ht_away_goals` and `result`.

**The form before kickoff.** `home_form` and `away_form` are the form of each
side over the played matches dated strictly before the fixture's own kickoff
date, five at most, across seasons, newest first. Each carries the `team` the
letters are seen from, `results` with one letter per match, `points`, `goals_for`
and `goals_against` over exactly those matches, and `matches`, the same fixtures
in the same order. The bound is the leakage rule: the fixture itself, any
fixture of the same day and every later one are excluded, so the form is the one
a reader could have seen before kickoff, and a played fixture shows the form it
had then rather than the form it has now.

**The record of the pair.** `head_to_head` applies the same rule to the pair:
`played` counts every prior meeting of the two clubs, at either venue and in any
season, strictly before the fixture date, while `matches` holds the ten most
recent of them, newest first, so `played` can exceed the length of `matches`.
The three counters are seen from this fixture's home side, not from the venue of
each earlier meeting: `home_side_wins` counts the meetings that side won,
`away_side_wins` the ones it lost and `draws` the ones neither won, so a club
that was the visitor in a prior meeting still counts its own wins.

**The forecasts.** `predictions` holds the standing stored forecast of each
model of the fixture, whatever it earned: `prediction_id`, `model`,
`model_version`, `created_at`, `as_of_date`, `probabilities` (of `home`, `draw`
and `away`) and `evaluation`. The list is ordered by the report position of the
model and then by its `model_version`, not in storage order. A played match
yields `scored` briefs, a scheduled one `pending` and a withdrawn one
`withdrawn`, and a model whose every row is `late` on a played or a scheduled
fixture has no row that can still stand, so its newest row is served as `late`.
`late` means the row was stamped at or after Madrid midnight of its match day,
so it was not a pre-kickoff forecast: a fixture whose date moved earlier than
its rows is one cause of that, not the rule. `superseded` is the outcome of a
row that a later as-of date replaced within its match and model, and it is never
served here: this section lists the standing row of each model, the newest row
that is not retired, so a model that carries one on-time row and a later `late`
row shows the on-time row. The evaluation is the core scorer's, so this section
cannot disagree with `/v1/predictions`, where the replaced rows of the ledger
are served when `latest_only` is `false`, or with `/v1/model-metrics/live`,
which counts them as `n_superseded`.

**The market price.** `odds` is the pre-closing 1x2 price of the fixture: the
`bookmaker`, `is_closing`, the three decimal prices `home`, `draw` and `away`,
and `implied`, the same price with the margin removed, so its `home`, `draw` and
`away` sum to one. The price is selected by bookmaker priority `pinnacle`, then
`market_avg`, then `bet365`, and `is_closing` is always `false`, because a
closing price is never eligible whatever its bookmaker. `odds` is null when the
fixture carries no eligible price, which is the normal state of a match still to
be played because the current sources publish odds only with results; no price
is invented.

An unknown fixture is a `404` with the detail `Match not found`, and a
`match_id` that is not an integer is a `422` whose `detail` is
`Request validation failed`. Neither sentence repeats the identifier.

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

## Predictions

### `GET /v1/predictions`

One page of stored live predictions, each with the outcome it earned.

| Parameter | Type | Default | Rule |
| --- | --- | --- | --- |
| `season` | string | the current season (see below) | Season code. An unknown code is a `404`. |
| `match_id` | integer | none | One fixture. An unknown identifier is a `404`. |
| `team_id` | integer | none (every club) | The fixtures a club played home or away. An unknown identifier is a `404`. |
| `matchday` | integer | none (every matchday) | The fixtures of one matchday of the season, `1`–`46`. A value outside the range is a `422`. |
| `model` | string | none (every model) | One registered model name. One outside the registry is a `422`. |
| `match_status` | string | none (every status) | `scheduled`, `played` or `withdrawn`. |
| `evaluation` | string | none (every outcome) | `pending`, `scored`, `superseded`, `late` or `withdrawn`. |
| `date_from` | date (`YYYY-MM-DD`) | none | Inclusive lower bound of the kickoff date, so a range covers whole days. |
| `date_to` | date (`YYYY-MM-DD`) | none | Inclusive upper bound. A `date_from` after `date_to` is a `422`. |
| `latest_only` | boolean | `true` | Project the rows the other filters selected onto one standing row per match and model, before `evaluation` is applied. |
| `limit` | integer | `100` | Page size, `1`–`500`. A value outside the range is a `422`. |
| `offset` | integer | `0` | Rows to skip. A negative value is a `422`. |

Every filter is optional and the ones given combine with `AND`. `matchday`
filters on the match's own matchday of the season, so it is the natural filter
for "the forecasts of the matchday ahead", and it narrows the same set `season`
and `team_id` narrow. Without `season` the page covers the same season the
standings endpoint opens on.

The two derived filters are ordered, and the order is part of the contract.
`latest_only` projects first and `evaluation` then restricts that projection. So
`evaluation=superseded` with `latest_only=true` is normally empty — a superseded
row never stands for its group — while `latest_only=false` is the whole ledger
the other filters selected. The default answers the question a dashboard asks:
one forecast per match and model.

`total` is the number of rows the filters matched before the page was sliced,
not the size of `items`. The order is the kickoff date ascending, then the match
identifier, then the model in registry order, then the as-of date descending, so
the ledger of one model lists its newest row first.

`evaluation` describes what a stored row earned, never what it stored:

| Value | Meaning |
| --- | --- |
| `pending` | The match has not been played and the row is the standing one for its match and model. |
| `scored` | The match has a result and the row was stamped before Madrid midnight of the match day. `score` carries its log loss, Brier score, ranked probability score and hit. |
| `superseded` | A later as-of date replaced the row. |
| `late` | The row was stamped at or after Madrid midnight of its match day, so it was not a pre-kickoff forecast. |
| `withdrawn` | The source no longer lists the fixture. |

A stored prediction is immutable: a served row is what the worker wrote, and
`evaluation` and `score` are derived on read by the one scorer the live metrics
endpoint also uses. `odds_source` is `null` when the match carried no eligible
pre-closing price, and `score` is `null` for every outcome other than `scored`:
a client renders "not scored" instead of a zero.

```json
{
  "items": [
    {
      "id": 9812,
      "match": {
        "id": 4311,
        "season": "2025-26",
        "kickoff_date": "2026-01-10",
        "kickoff_time": "21:00:00",
        "matchday": 19,
        "status": "scheduled",
        "home_team": { "id": 12, "name": "Valencia" },
        "away_team": { "id": 5, "name": "Barcelona" },
        "home_goals": null,
        "away_goals": null,
        "ht_home_goals": null,
        "ht_away_goals": null,
        "result": null
      },
      "model": "dixon_coles",
      "model_version": "1",
      "as_of_date": "2026-01-08",
      "created_at": "2026-01-08T06:00:12.418003Z",
      "probabilities": { "home": 0.2814, "draw": 0.2602, "away": 0.4584 },
      "odds_source": null,
      "evaluation": "pending",
      "score": null
    }
  ],
  "total": 1,
  "limit": 100,
  "offset": 0
}
```

### `GET /v1/predictions/{prediction_id}`

One stored prediction with its run and the configuration the model recorded at
write time. The body is the row above plus `run_id` and `model_config`, so a
reader can tell which settings produced the probabilities without a second
request. The group-correct evaluation is served for the single row too: a row a
later as-of date replaced is `superseded` here exactly as it is in the ledger. An
unknown identifier is a `404` and a segment that is not an integer is a `422`.

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

### `GET /v1/model-metrics/live`

The in-play report of one season: every stored prediction row evaluated as it
stands, scored per model, on the matches all models share, and against the
market.

| Parameter | Type | Default | Rule |
| --- | --- | --- | --- |
| `season` | string | the current season (see below) | Season code. An unknown code is a `404`. |

Two of the model rows are shown, both covering the same twelve matches, so both
stand on the shared set; the market reference covers the ten of those twelve
that carry a price.

```json
{
  "evaluated_as_of": "2026-11-16",
  "models": [
    {
      "model": "home_win",
      "model_version": "1",
      "scores": {
        "n_matches": 12,
        "log_loss": 1.0522,
        "brier": 0.6204,
        "rps": 0.2318,
        "accuracy": 0.3333
      }
    },
    {
      "model": "dixon_coles",
      "model_version": "1",
      "scores": {
        "n_matches": 12,
        "log_loss": 0.9741,
        "brier": 0.5683,
        "rps": 0.1802,
        "accuracy": 0.5
      }
    }
  ],
  "common": {
    "match_count": 12,
    "models": [
      {
        "model": "home_win",
        "model_version": "1",
        "scores": {
          "n_matches": 12,
          "log_loss": 1.0522,
          "brier": 0.6204,
          "rps": 0.2318,
          "accuracy": 0.3333
        }
      },
      {
        "model": "dixon_coles",
        "model_version": "1",
        "scores": {
          "n_matches": 12,
          "log_loss": 0.9741,
          "brier": 0.5683,
          "rps": 0.1802,
          "accuracy": 0.5
        }
      }
    ]
  },
  "market_reference": {
    "model": "market",
    "model_version": "1",
    "scores": {
      "n_matches": 10,
      "log_loss": 0.9607,
      "brier": 0.5602,
      "rps": 0.1772,
      "accuracy": 0.6
    },
    "stored_before_kickoff": false,
    "match_count": 10,
    "matches_without_odds": 2
  },
  "counts": {
    "n_rows": 80,
    "n_scored": 60,
    "n_pending": 20,
    "n_superseded": 0,
    "n_late": 0,
    "n_withdrawn": 0
  }
}
```

**Which season by default.** The same rule the standings endpoint applies:
without `season`, the answer is the greatest `start_year` among the seasons that
have at least one match, so both resources open on the season ingestion has
loaded. When no season has a match the response is a `404` with the detail
`No season with matches`; a code no season carries is a `404` with the detail
`Season not found`. The shared problem responses declare a `422` for this route
even though no validation failure can occur there: its only parameter is an
unconstrained string, and an unknown season code is a `404`, the same note the
`## Conventions` section makes for `GET /v1/teams`.

**What live means.** "Live" is the in-play scoring of the stored prediction rows
of one season: every row is evaluated as it stands, and the numbers are the ones
the worker's `predict --report` prints, because both call the same core
function. The season bounds the match identifiers that are read, never another
season's predictions. `evaluated_as_of` is the Madrid date of the request.

**The counts.** `counts` says which rows the metrics cover: `n_rows` is every
evaluated prediction row, `n_scored` the rows with a result and an on-time
prediction, `n_pending` the on-time rows that stand for a fixture still to be
played, `n_superseded` the on-time rows a later as-of date replaced, `n_late`
the rows stamped at or after Madrid midnight of their match day and
`n_withdrawn` the rows whose match was withdrawn. Only a scored row carries a
score.

**The models.** `models` has one entry per model and version with scored rows,
each carrying `model`, `model_version` and `scores`, and each computed on its own
match set. The rows are ordered by the report position of the model, as the
model registry lists the names, and then by `model_version`, not in storage
order; a model name the registry does not list sorts last. `scores` carries
`n_matches`, the scored matches behind the numbers, and the mean `log_loss`,
`brier` and `rps`, with `accuracy` the share of correct top outcomes. As in the
backtest report above, the three errors are lower is better and the accuracy
higher is better.

**The comparison.** `common` is the same comparison restricted to the matches
every model scored, which is the fair one: `match_count` is the size of the
shared set and `models` holds one entry per model on it, in that same order. The
shared set is built per model name, so two versions of one model contribute the
union of their matches to it, while each entry still covers only the shared
matches it holds a scored row for and an entry with none is omitted. It is null
when no match is shared.

**The market reference.** `market_reference` is the market benchmark recomputed
from the pre-closing 1x2 odds of the played matches in that cohort, with the
margin removed. It carries `model` and `model_version` of the market model,
`scores` in the same shape as the model rows, `match_count`, the number of
matches it covers, and `matches_without_odds`, the matches it had to skip for
missing odds, so its `match_count` is below the `match_count` of `common` when a
shared match carried no price; a skipped match leaves the model entries above
untouched. `stored_before_kickoff` is always `false`, because the reference is a
benchmark recomputed on read and not a prediction the worker stored before
kickoff. It is null when no odds are available.

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

