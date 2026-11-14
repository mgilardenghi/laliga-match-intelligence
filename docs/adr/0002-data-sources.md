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

## Amendment (2026-11-13)

Live checks during the M1 database work corrected one claim above and added parsing rules that ingestion must follow.

- **There is no referee column.** None of the 22 `SP1` files from 2005/06 to 2026/27 carries a referee field, so the "referee" item in the table above is wrong. Referee analytics are deferred until a free referee source is verified (see the MVP specification, sections 5 and 6). No referee table and no referee column exist in the schema.
- **Columns drift by season.** Header sets, file widths, date formats (`DD/MM/YY` through 2015/16, `DD/MM/YYYY` from 2018/19), byte-order marks (from 2024/25) and odds families changed over the 22 files; the families were renamed more than once (`BbAv*` and `Avg*`, `BbMx*` and `Max*`, `PS*` and `PP*`). Ingestion parses by header name, with a small required core and optional fields, never by column position, and ignores unknown extra columns.
- **Downloads redirect.** A request for an `SP1` URL answers `302`; the body arrives only when redirects are followed. The client follows redirects and requires a final `200`.
- **openfootball carries no usable kickoff instant.** For every comparable 2026/27 fixture its kickoff time is one hour later than the football-data time, and its round dates are anchors rather than the real date. openfootball therefore supplies only the date and the matchday of a scheduled fixture; football-data supplies the date and the time of a played match, and an openfootball file never overwrites or downgrades a played row.
- **Historical seasons have no matchday.** The files before the current season carry no matchday, so matchday is filled only for the current season.
