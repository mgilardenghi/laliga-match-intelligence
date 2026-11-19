# Models

Every prediction is a probability triple for a home win, a draw and an away
win. Every model is fitted only on matches strictly before its cutoff, so a fit
never sees a match the model prices. The code lives in
`packages/core/src/lmi_core`.

## The registry

`packages/core/src/lmi_core/modeling/registry.py` declares the five model names
in report order: `home_win`, `base_rate`, `market`, `elo` and `dixon_coles`,
with kind `baseline`, `baseline`, `market`, `model` and `model`. A constructor
is imported lazily, so a module is only loaded when its model is built.

Every model subclasses `Model` in
`packages/core/src/lmi_core/modeling/base.py`. `fit` and `predict` are final
methods that enforce the leakage guards; a subclass implements the `_fit` and
`_predict` hooks. Dixon-Coles also exposes a public `score_matrix` method, which
the simulation calls directly.

The shared data types are in `packages/core/src/lmi_core/modeling/types.py`.
`Fixtures` carries the match id, the kickoff date, the season id, both team ids,
an `(n, 3)` array of decimal home/draw/away odds where an all-NaN row means no
odds, and the odds source. `History` adds the goals and the result index (0 home
win, 1 draw, 2 away win).

`packages/core/src/lmi_core/modeling/data.py` loads the played matches from
PostgreSQL and picks one 1x2 odds row per match: a non-closing price, market
`1x2`, bookmaker priority `pinnacle`, then `market_avg`, then `bet365`.
`market_max` is never used.

## home_win

`packages/core/src/lmi_core/modeling/baselines.py`. Fits nothing and assigns
probability 1 to the home outcome and 0 to the other two. It is the degenerate
reference: its log loss is `-ln(1e-15)` on every away win or draw, because the
scoring clips probabilities at `1e-15`.

## base_rate

`packages/core/src/lmi_core/modeling/baselines.py`. Predicts the home-win, draw
and away-win frequencies of its own training window, and `1/3` each when the
history is empty. It returns the same triple for every fixture. It is the
non-degenerate reference.

## market

`packages/core/src/lmi_core/modeling/market.py`. Uses the odds only; `fit` does
nothing because odds are pre-kickoff information. `remove_margin` divides each
implied probability by the row total, which is proportional margin removal:
`p_i = (1/o_i) / sum_j (1/o_j)`. A fixture without a complete odds row yields an
all-NaN forecast and is counted as uncovered (`n_no_forecast`); a non-positive
or non-finite price raises `ValueError`. There is no silent fallback to another
model.

## elo

`packages/core/src/lmi_core/modeling/elo.py`. Two steps.

1. A rating replay in date order: every match on a date is priced from that
   date's opening snapshot, then both ratings move by `K * (S - E)`, where `S`
   is 1 for a win, 0.5 for a draw and 0 for a loss, and the expected score is
   `E = 1 / (1 + 10**-d)` with `d = (home_rating + 60 - away_rating) / 400`.
2. An ordered logit on the rating gap with two cut points. The away mass is
   `P(away) = sigma(c1 - b*d)` and the away-or-draw mass is
   `P(away or draw) = sigma(c2 - b*d)`; the home win and the draw are the two
   differences, so the three probabilities sum to 1 by construction.

Constants, all in that module: start rating 1500.0, K 20.0, home advantage 60.0,
rating scale 400.0, newcomer sample 3. The fit is L-BFGS-B on the negative log
likelihood, started every fit from the fixed point `log_b = 0`, `c1 = -0.8`,
`log_gap = ln(1.9)`; a non-finite solution falls back to that start. No
iteration or tolerance limits are passed, so the SciPy defaults apply. A team
with no rating gets the mean of the 3 lowest current ratings among the teams of
the newest season in the history, or 1500.0 when there is none. The seed is
recorded but never used: the model is deterministic.

## dixon_coles

`packages/core/src/lmi_core/modeling/dixon_coles.py`. A Poisson goals model with
a low-score correction.

- Expected goals: `lambda_home = exp(mu + gamma + alpha_home - beta_away)` and
  `lambda_away = exp(mu + alpha_away - beta_home)`. `alpha` is attack, `beta` is
  defence and `gamma` is the home advantage.
- Low scores: a correction factor `tau` applies to the four scores 0-0, 0-1,
  1-0 and 1-1, controlled by `rho`; values are floored at 1e-10 so the logarithm
  stays finite.
- Time decay: each match is weighted `exp(-0.0019 * days_before_cutoff)`.
- History window: matches with `cutoff - 1095 days <= kickoff_date < cutoff`, so
  three years.
- Fit: the weighted negative log likelihood plus a ridge penalty
  `0.01 * sum(alpha^2 + beta^2)`, with an analytic gradient, L-BFGS-B, and `rho`
  bounded to `[-0.25, 0.25]`. The start is fixed and never warm-started: zeros
  for alpha, beta, gamma and rho, and
  `mu = log(max(total_goals / (2 * matches), 1e-3))`. A non-finite solution
  falls back to the start. SciPy defaults
  apply to iterations and tolerance.
- Score grid: goals from 0 to 10 per side, 121 cells, renormalised, negative
  corrected cells set to 0.
- A team with no fitted strength takes the mean attack and defence of the 3
  weakest fitted teams that played the window's most recent season; both are 0
  when the window is empty.
- `score_matrix()` returns the per-fixture `(n, 11, 11)` grid, and the outcome
  probabilities are the sum of the lower triangle (home win), the diagonal
  (draw) and the upper triangle (away win).

## Walk-forward evaluation

`packages/core/src/lmi_core/evaluation/walkforward.py`, `backtest.py` and
`report.py`. Dates, not matchdays: the historical seasons carry no matchday.

- Cutoffs are Mondays: the first one is the Monday on or before the earliest
  scored kickoff date, then one every 7 days (`step_days`), and they must be
  strictly ascending.
- At each cutoff a fresh model is constructed and fitted with
  `model.fit(data.select(data.kickoff_date < start), cutoff)`, so only matches
  strictly before the cutoff are visible, and it predicts the window
  `start <= kickoff_date < start + 7 days`. The window of one cutoff is scored
  by a model that never saw it or anything later.
- The scored seasons are the ones that start in or after `from_year`, 2007 by
  default. Earlier seasons are warm-up: they are never scored, but they stay in
  every fit history.
- The worker command refuses to run when the scored range holds fewer than 380
  played matches, so the backtest needs the full backfill.
- The report carries a sha256 fingerprint of the canonically ordered input rows,
  so two runs over the same data are comparable.

## Metrics

`packages/core/src/lmi_core/evaluation/metrics.py`. Every metric is the mean
over the rows that carry a forecast; an all-NaN row is dropped, and a partly-NaN
row raises.

- Log loss: `-mean(log(clip(p_result, 1e-15, 1)))`.
- Brier: `mean(sum_k (p_k - y_k)^2)` over the three outcomes, in `[0, 2]`.
- Ranked probability score: `mean(sum_{k=1..2} (P_k - Y_k)^2 / 2)` over the
  cumulative home and home-or-draw sums.
- Accuracy: the share of rows where the most likely outcome is the result, ties
  broken in the order home, draw, away.
- Calibration: 10 equal-width probability bins for each of the three outcomes,
  30 rows per model; each row reports the number of rows, the mean forecast and
  the observed frequency, and an empty bin reports none. The backtest console
  report also prints the expected calibration error per model and outcome,
  `sum(n_bin / N * abs(observed - mean_predicted))` over the non-empty bins. The
  dashboard draws the bins, not that aggregate.

No metric is designated as primary in the code: all four are stored per row and
shown side by side.

Two subsets, `all` and `common`, are stored for every model, season and overall
row; a scope with no scored match produces no row.
`all` counts the scored matches that model forecast. `common` counts only
the matches that every model forecast, so the models can be compared on the same
fixtures. `n_matches` is the number of rows a metric averages; `n_fixtures` is
every fixture in scope, including the ones without a forecast.

The backtest command stores its metrics in `backtest_runs`, `backtest_metrics`
and `backtest_calibration`; `GET /v1/model-metrics` serves the latest succeeded
run. Per-match backtest predictions are not stored.

## Leakage guards

- `Model.fit` is final and raises `LeakageError` when the history holds any
  match dated on or after the cutoff date: the cutoff day itself is excluded, so
  a same-day result can never enter a fit. The guard is in
  `packages/core/src/lmi_core/modeling/base.py` and the tests are
  `packages/core/tests/modeling/test_base_guards.py`.
- `Model.predict` rejects a history argument and any fixture dated before the
  cutoff, so a model cannot be asked to price a match it was not meant to see.
- The backtest driver slices the data by `kickoff_date`, a date column, and
  never reads the clock.
- Live predictions use the Madrid calendar:
  `packages/core/src/lmi_core/clock.py` defines `Europe/Madrid` and
  `kickoff_boundary(day)`, which returns local Madrid midnight as a UTC instant,
  DST-correct. The CLI passes `as_of = madrid_today()`, the horizon is 14 days by
  default and bounded to 1-60, and the matches predicted are the scheduled ones
  with `as_of < kickoff_date <= as_of + horizon`, on history strictly before
  `as_of`.
- Immutability is enforced by the database, not only by code: migration
  `packages/core/src/lmi_core/db/migrations/versions/0003_live.py` installs
  `predictions_immutable` (BEFORE UPDATE OR DELETE) and
  `predictions_no_truncate` on the `predictions` table, both raising
  `lmi: <op> on <table> is not allowed, rows are immutable`. An insert guard
  rejects a prediction whose `as_of_date` is not the current Madrid date, and
  one created at or after Madrid midnight of the match date. The rows are a
  natural key of `(match_id, model, as_of_date)` and inserts use
  `ON CONFLICT DO NOTHING`.
- A stored prediction row is never updated: a re-run writes new rows for a new
  `as_of_date`.

## Season simulation

`packages/core/src/lmi_core/simulation/engine.py` holds the pure computation and
`service.py` the database and CLI glue.

- Model: Dixon-Coles only. `service.py` fixes the model name, and the database
  allows only that name for a simulation run.
- What is sampled: a full scoreline for every remaining fixture, drawn from the
  fixture's `(11, 11)` Dixon-Coles score grid, 121 cells, by inverse CDF from one
  `numpy.random.PCG64(seed)` stream. The global generator is never used.
- Fixed versus sampled: a match already played strictly before the as-of date
  counts in every run and is never sampled. Every other non-withdrawn fixture is
  simulated with `sim_date = max(kickoff_date, as_of_date)`.
- Runs and seed: `--runs` defaults to 10000 and is bounded to 100-100000;
  `--seed` defaults to 0. The same arguments over the same data produce
  identical rows.
- Ranking: every simulated season is ranked by
  `lmi_core.standings.compute_standings`, the only ranking implementation in the
  code base, so the simulation cannot drift from the real table.
  `compute_standings` groups teams by points and, inside a group, ranks
  head-to-head, then goal difference, then goals for, then the team name and id.
  For a group of exactly two, head-to-head enters through goal difference only.
- Output per team: mean points, the points quantiles 5, 25, 50, 75 and 95, the
  points distribution, mean position, the position distribution, the title
  probability and the European and relegation probabilities. The qualification
  thresholds default to 4 Champions League places, 6 European places and 3
  relegation places.
- Projected order: mean position, then higher mean points, then the team name
  and id.
- The stored run also records the fixed and simulated fixture counts, the
  withdrawn count and the last complete matchday, plus a data fingerprint and
  the model version and configuration.
- Per-fixture win, draw and loss probabilities are model-exact. They come from
  that fixture's `(11, 11)` scoreline grid: the sum of the cells with more home
  goals, the trace for a draw and the sum of the cells with more away goals,
  each normalised by the sum of the three. They add up to one and carry no
  sampling noise.
- The next-match detail is stored as integer counts from the run's own draws,
  for every team with a simulated fixture: how many runs reached each of the
  three outcomes of its next fixture, and how many of those also ended in the
  title, the European places and the relegation places, plus the summed final
  points. The API divides them.
- "Next match" means the first simulated fixture in `(kickoff_date, id)` order
  that is dated on or after the as-of date, the same rule the API applies with
  today's date to pick the fixture the focus page lists first. A postponed fixture
  dated before it is still simulated but is not named; the rule reads the date,
  never the match status. A club with no fixture dated on or after the as-of date
  stores no next-match rows.
- An outcome that no run reached is stored as a zero row, not left out.
- A run that predates revision `0004` has no detail rows, so those fields stay
  null for it.

## Where each parameter lives

| Parameter | Value | Where |
| --- | --- | --- |
| Model names and order | `home_win`, `base_rate`, `market`, `elo`, `dixon_coles` | `packages/core/src/lmi_core/modeling/registry.py:15-20` |
| Elo start rating | 1500.0 | `packages/core/src/lmi_core/modeling/elo.py:42` |
| Elo K factor | 20.0 | `packages/core/src/lmi_core/modeling/elo.py:45` |
| Elo home advantage | 60.0 | `packages/core/src/lmi_core/modeling/elo.py:48` |
| Elo rating scale | 400.0 | `packages/core/src/lmi_core/modeling/elo.py:51` |
| Elo newcomer sample | 3 | `packages/core/src/lmi_core/modeling/elo.py:54` |
| Dixon-Coles time decay | 0.0019 per day | `packages/core/src/lmi_core/modeling/dixon_coles.py:66` |
| Dixon-Coles history window | 1095 days | `packages/core/src/lmi_core/modeling/dixon_coles.py:69` |
| Dixon-Coles ridge penalty | 0.01 | `packages/core/src/lmi_core/modeling/dixon_coles.py:72` |
| Dixon-Coles rho bound | 0.25 | `packages/core/src/lmi_core/modeling/dixon_coles.py:75` |
| Dixon-Coles score grid | 0 to 10 goals | `packages/core/src/lmi_core/modeling/dixon_coles.py:78` |
| Dixon-Coles newcomer teams | 3 | `packages/core/src/lmi_core/modeling/dixon_coles.py:87` |
| Odds bookmaker priority | `pinnacle`, `market_avg`, `bet365` | `packages/core/src/lmi_core/modeling/data.py:33` |
| Probability sum tolerance | 1e-9 | `packages/core/src/lmi_core/modeling/base.py:20` |
| Scoring probability clip | 1e-15 | `packages/core/src/lmi_core/evaluation/metrics.py:22` |
| Walk-forward step | 7 days | `packages/core/src/lmi_core/evaluation/walkforward.py:21` |
| First scored season | 2007 | `packages/core/src/lmi_core/evaluation/report.py:59` |
| Calibration bins | 10 per outcome | `packages/core/src/lmi_core/evaluation/metrics.py:68` |
| Minimum scored matches | 380 | `apps/worker/src/lmi_worker/backtest.py:66` |
| Live prediction horizon default | 14 days, bounded 1 to 60 | `packages/core/src/lmi_core/live/predict.py:28-32` |
| Simulation runs default | 10000, bounded 100 to 100000 | `apps/worker/src/lmi_worker/simulate.py:53-59` |
| Simulation seed default | 0 | `packages/core/src/lmi_core/simulation/__init__.py:22` |
| Qualification thresholds | UCL 4, Europe 6, relegation 3 | `packages/core/src/lmi_core/simulation/engine.py:57-59` |
