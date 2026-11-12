# ADR-0002: Data sources

Status: accepted (2026-11-12)

## Context

The project must not stall because a data source is unavailable or paid. Sources were tested live on 2026-11-12.

| Source | Result |
|---|---|
| football-data.co.uk CSVs (`SP1`) | Free, no key. 2026/27 current to the last matchday. Per match: scores, shots, shots on target, corners, fouls, cards, referee, bookmaker odds, xG in recent files. Seasons since 1993/94. No formal license ("my data is free"). |
| openfootball `football.json` | Free, no key, CC0. Full 2026/27 fixture list with kickoff times. Scores lag several weeks. |
| StatsBomb Open Data | La Liga ends 2020/21 and covers mostly Barcelona matches. Rejected as a main source. |
| football-data.org, TheSportsDB, API-Football | Need a key, or the free tier is truncated. Not used in the MVP. |

## Decision

- football-data.co.uk is the source of results, match statistics and odds.
- openfootball is the source of the future fixture list only.
- The worker downloads raw files at runtime. Raw data is never committed to git.
- The README credits both sources.
- Team names are normalized with a mapping table owned by this project.

## Consequences

- No player or squad data in the MVP. Phase 2 needs a verified source first.
- If football-data.co.uk changes format or stops updating, ingestion fails loudly and the run is recorded as failed.
- Commercial use would require licensed data.
