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
| `detail` | A sentence written by the API, never a stack trace or a SQL fragment. The matches and standings routes use a fixed sentence that never repeats a value the client sent; the older seasons and teams routes quote the value they were given, which a follow-up task unifies. |
| `errors` | Present only on a validation failure: one entry per rejected value with `loc`, `msg` and `type`. The rejected value itself is never echoed. |

The statuses a route can return are declared in its OpenAPI responses, so the
generated client models the real body instead of a default error envelope:

| Route | `404` | `422` |
| --- | --- | --- |
| `GET /v1/seasons` | — | — |
| `GET /v1/teams` | unknown season code | declared, not reachable (its only parameter is an unconstrained string) |
| `GET /v1/teams/{team_id}` | unknown team | non-integer identifier |
| `GET /v1/matches` | unknown season code or team identifier | a query value outside its rule |
| `GET /v1/matches/{match_id}` | unknown match | non-integer identifier |
| `GET /v1/standings` | unknown season code, or no season with matches | malformed `as_of` |

An unexpected failure is a `500` whose body carries no detail: the cause is in
the server log.

A route uses the shared problem response set, so `GET /v1/teams` declares a
`422` in the OpenAPI document even though no validation failure can occur there:
its only parameter is an unconstrained string, and an unknown season code is a
`404`.

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
