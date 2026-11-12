# MVP specification

Status: accepted (2026-11-12)

## Goal

A local system that ingests Spanish La Liga data, predicts matches, simulates the rest of the season, and measures its own accuracy against real results and the betting market. A team focus mode gives a deep view of one selected club (default: Valencia CF).

The project is a public portfolio piece. It runs on localhost with free, verified data sources only.

## Users and questions

| User | Question the product answers |
|---|---|
| Fan or analyst following one club | Why is the team where it is? Is it bad luck or bad play? What happens next? |
| Analyst preparing a match | What does the opponent look like? What are the outcome probabilities? |
| Interviewer reviewing the repo | Is the model honest, evaluated, and served correctly? |

## In scope

### 1. Ingestion (worker)

- Download match CSVs from football-data.co.uk (La Liga, `SP1`), from 2005/06 to the current season.
- Download the current-season fixture list from openfootball (`football.json`, `es.1.json`).
- Normalize team names across sources with a mapping table.
- Idempotent runs: re-running never duplicates rows. Every run is recorded (source, started, finished, rows, status).
- Raw files are downloaded at runtime and never committed (see ADR-0002).

### 2. Models

- Baselines: "home team always wins", and market-implied probabilities from bookmaker odds (margin removed).
- Elo rating.
- Dixon-Coles (Poisson goals model with low-score correction and time decay).
- Every prediction uses only information available before kickoff. No data leakage.

### 3. Evaluation

- Walk-forward backtest per season: fit on data before matchday N, predict matchday N, move on.
- Metrics: log loss, Brier score, ranked probability score (RPS), calibration curve.
- Compare every model against the baselines and the market.
- Live tracking: predictions for upcoming 2026/27 matches are stored with a creation timestamp before kickoff and scored after the result arrives. Stored predictions are never modified.

### 4. Season simulation

- Monte Carlo simulation of the remaining fixtures (default 10,000 runs), run as an asynchronous worker job.
- Output per team: probability of title, European places, relegation; projected points distribution and final table.
- History of simulation results per matchday, to chart how probabilities change over the season.

### 5. League analytics

- Current table computed from results.
- Per team: expected points vs actual points, goals vs xG where available, shots and shots on target for and against, home/away split, form, discipline.
- Referee tendencies (cards and fouls per match).

### 6. Team focus mode

Select one team (default Valencia CF) and see:

- Season diagnosis: points vs expected points, rolling 5-match trends, home vs away, results vs top / middle / bottom opponents, discipline, referee history.
- Multi-season trend since 2005/06: which metrics declined and when.
- Relegation race: relegation probability per matchday, direct rivals comparison, remaining fixture difficulty, survival points threshold derived from historical 17th-place totals.
- Next match preview: model probabilities, opponent profile, and how much the result moves relegation probability.

### 7. API (FastAPI, OpenAPI contract)

Resource groups: `teams`, `matches`, `standings`, `predictions`, `simulations`, `model-metrics`, `teams/{id}/focus`, `matches/{id}/preview`, `ingestion-runs`. Exact endpoints are defined in M2 and M3 tasks.

### 8. Dashboard (Next.js)

Pages: current and projected table, next matchday predictions, team page, team focus page, match preview, model performance (backtest and live vs market).

The dashboard reads data only through the API, with a client generated from the OpenAPI contract.

## Out of scope (later phases)

- Player and squad data (Phase 2, only after the data source is verified).
- RAG and LLM agent for written match reports.
- Kubernetes, Terraform, GCP deployment.
- StatsBomb event data.
- User authentication.
- Other leagues.

## Acceptance criteria

- `docker compose up` starts Postgres, Redis, API, worker and dashboard on a clean machine.
- One command runs a full ingestion; a second run adds no duplicates.
- One command runs the backtest and prints metrics per model and season; results are reproducible with a fixed seed.
- The model performance page shows each model vs baselines and market.
- Tests cover team-name normalization, model math, leakage guards, and the API contract.
- CI (GitHub Actions) runs lint, type checks and tests on every pull request.
- README has an architecture diagram, data attribution, and how to run.

## Milestones

| Milestone | Deliverable |
|---|---|
| M0 | Monorepo skeleton, Docker Compose, CI, tooling, version pins |
| M1 | Ingestion, normalization, database schema and migrations |
| M2 | Core API: teams, matches, standings |
| M3 | Models, backtest, metrics |
| M4 | Live predictions and asynchronous season simulation |
| M5 | Dashboard, including team focus mode |
| M6 | Docs, diagrams, polish |

## Open items

- Job queue library: ADR-0004 (proposed).
- First season with xG in the football-data.co.uk CSVs: verify in M1. Models do not depend on xG; it is added where present.
