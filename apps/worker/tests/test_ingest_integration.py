"""Integration tests for the ingestion runner, the status report and the CLI.

The four scenarios here are the M1 acceptance subset of this block: a full
football-data and openfootball load on a migrated and seeded database, a second
identical run that changes nothing, a later file that moves ten scheduled
matches into the played state, and an unknown team name that fails the whole
file before any write.

The tests need a live PostgreSQL and are opt-in through ``-m integration``. The
``migrated_engine`` fixture drops and recreates the public schema, so it refuses
a database that is not explicitly disposable and fails instead of skipping when
the ``POSTGRES_*`` variables are unset.
"""

from __future__ import annotations

from datetime import date, time
from typing import TYPE_CHECKING

import pytest
from ingest_support import (
    FakeFetcher,
    canonical_teams,
    flatten,
    football_data_csv,
    openfootball_json,
    round_robin,
)
from lmi_core.db.models import (
    IngestionRejection,
    IngestionRun,
    Match,
    MatchOdd,
    MatchStat,
    Season,
    Team,
)
from lmi_worker.ingest.pipeline import JobResult, run_jobs
from lmi_worker.ingest.plan import Job, source_url
from lmi_worker.seasons import SeasonCode
from lmi_worker.status import collect_status
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

if TYPE_CHECKING:
    from conftest import MigratedDatabase

pytestmark = pytest.mark.integration

CURRENT = SeasonCode.parse("2026-27")
PREVIOUS = SeasonCode.parse("2025-26")
START = date(2026, 8, 15)

TEAMS = canonical_teams()
ROUNDS = round_robin(TEAMS)
FIXTURES = flatten(ROUNDS)
MATCHDAY_BY_PAIR = {
    pair: matchday
    for matchday, matchday_fixtures in enumerate(ROUNDS, start=1)
    for pair in matchday_fixtures
}

OF_URL = source_url(Job(source="openfootball", season=CURRENT))
FD_CURRENT_URL = source_url(Job(source="football_data", season=CURRENT))
FD_PREVIOUS_URL = source_url(Job(source="football_data", season=PREVIOUS))


def _current_csv(played: int) -> bytes:
    """Return the current-season football-data file with ``played`` matches."""
    return football_data_csv(
        FIXTURES[:played],
        start=START,
        include_xg=True,
        bom=True,
        crlf=True,
    )


def _previous_csv() -> bytes:
    """Return a small complete football-data season of the previous year."""
    fixtures = flatten(round_robin(TEAMS[:4]))
    return football_data_csv(fixtures, start=date(2025, 8, 15))


def _bodies(played: int = 30) -> dict[str, bytes]:
    """Return the three source files of the full-flow scenario."""
    return {
        OF_URL: openfootball_json(TEAMS, start=START),
        FD_CURRENT_URL: _current_csv(played),
        FD_PREVIOUS_URL: _previous_csv(),
    }


def _full_flow(db: MigratedDatabase, played: int = 30) -> tuple[FakeFetcher, list[JobResult]]:
    """Run openfootball then both football-data seasons and return the results."""
    fetcher = FakeFetcher(_bodies(played))
    jobs = [
        Job(source="openfootball", season=CURRENT),
        Job(source="football_data", season=CURRENT),
        Job(source="football_data", season=PREVIOUS),
    ]
    return fetcher, run_jobs(db.engine, jobs, fetcher, lambda: None)


def _match_rows(
    db: MigratedDatabase, season_code: str
) -> list[tuple[str, str, str, time | None, int | None]]:
    """Return home, away, status, kickoff time and matchday of one season."""
    home = aliased(Team)
    away = aliased(Team)
    with db.factories() as session:
        rows = session.execute(
            select(
                home.name,
                away.name,
                Match.status,
                Match.kickoff_time,
                Match.matchday,
            )
            .join(Season, Season.id == Match.season_id)
            .join(home, home.id == Match.home_team_id)
            .join(away, away.id == Match.away_team_id)
            .where(Season.code == season_code)
        ).all()
    return [(str(row[0]), str(row[1]), str(row[2]), row[3], row[4]) for row in rows]


def _count(
    db: MigratedDatabase,
    model: type[Match]
    | type[MatchStat]
    | type[MatchOdd]
    | type[IngestionRun]
    | type[IngestionRejection],
) -> int:
    """Return the number of rows of one table."""
    with db.factories() as session:
        total = session.scalar(select(func.count()).select_from(model))
    return int(total or 0)


def _max_updated_at(db: MigratedDatabase) -> object:
    """Return the newest ``matches.updated_at`` value."""
    with db.factories() as session:
        return session.scalar(select(func.max(Match.updated_at)))


def _runs(db: MigratedDatabase) -> list[IngestionRun]:
    """Return every run, oldest first."""
    with db.factories() as session:
        return list(session.scalars(select(IngestionRun).order_by(IngestionRun.started_at)))


def test_full_flow_keeps_openfootball_matchdays_and_football_data_times(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine

    fetcher, results = _full_flow(db)

    assert [result.status for result in results] == ["succeeded", "succeeded", "succeeded"]
    assert [result.rows_read for result in results] == [380, 30, 12]
    assert [result.rows_upserted for result in results] == [380, 30, 12]
    assert [result.rows_removed for result in results] == [0, 0, 0]
    assert fetcher.requested == [OF_URL, FD_CURRENT_URL, FD_PREVIOUS_URL]
    assert _count(db, Match) == 380 + 12
    assert _count(db, MatchStat) == 30 + 12
    assert _count(db, MatchOdd) == (30 + 12) * 4

    rows = _match_rows(db, CURRENT.code)
    assert len(rows) == 380
    by_pair = {
        (home, away): (status, kickoff_time, matchday)
        for home, away, status, kickoff_time, matchday in rows
    }
    for pair in FIXTURES[:30]:
        assert by_pair[pair] == ("played", time(21, 0), MATCHDAY_BY_PAIR[pair])
    for pair in FIXTURES[30:]:
        status, kickoff_time, matchday = by_pair[pair]
        assert status == "scheduled"
        assert kickoff_time is None
        assert matchday == MATCHDAY_BY_PAIR[pair]

    previous_rows = _match_rows(db, PREVIOUS.code)
    assert len(previous_rows) == 12
    assert all(
        status == "played" and kickoff_time == time(21, 0) and matchday is None
        for _, _, status, kickoff_time, matchday in previous_rows
    )


def test_a_second_identical_run_changes_nothing(migrated_engine: MigratedDatabase) -> None:
    db = migrated_engine
    _full_flow(db)
    before = collect_status(db.engine)
    before_updated = _max_updated_at(db)
    before_counts = (
        _count(db, Match),
        _count(db, MatchStat),
        _count(db, MatchOdd),
    )

    _, second = _full_flow(db)

    assert [result.status for result in second] == ["succeeded", "succeeded", "succeeded"]
    assert [result.rows_upserted for result in second] == [0, 0, 0]
    assert [result.rows_removed for result in second] == [0, 0, 0]
    assert [result.rows_rejected for result in second] == [0, 0, 0]
    after = collect_status(db.engine)
    assert after.seasons == before.seasons
    assert after.tables == before.tables
    assert (
        after.digest_matches,
        after.digest_stats,
        after.digest_odds,
        after.digest,
    ) == (
        before.digest_matches,
        before.digest_stats,
        before.digest_odds,
        before.digest,
    )
    assert _max_updated_at(db) == before_updated
    assert (
        _count(db, Match),
        _count(db, MatchStat),
        _count(db, MatchOdd),
    ) == before_counts


def test_a_later_football_data_file_plays_ten_more_scheduled_matches(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine
    current = Job(source="football_data", season=CURRENT)
    first = FakeFetcher(
        {
            OF_URL: openfootball_json(TEAMS, start=START),
            FD_CURRENT_URL: _current_csv(30),
        }
    )
    run_jobs(
        db.engine,
        [Job(source="openfootball", season=CURRENT), current],
        first,
        lambda: None,
    )
    assert sum(1 for row in _match_rows(db, CURRENT.code) if row[2] == "played") == 30

    second = FakeFetcher({FD_CURRENT_URL: _current_csv(40)})
    results = run_jobs(db.engine, [current], second, lambda: None)

    assert results[0].status == "succeeded"
    assert results[0].rows_upserted == 10
    assert results[0].rows_removed == 0
    rows = _match_rows(db, CURRENT.code)
    assert sum(1 for row in rows if row[2] == "played") == 40
    assert sum(1 for row in rows if row[2] == "scheduled") == 340
    by_pair = {
        (home, away): (status, kickoff_time, matchday)
        for home, away, status, kickoff_time, matchday in rows
    }
    for pair in FIXTURES[30:40]:
        assert by_pair[pair] == ("played", time(21, 0), MATCHDAY_BY_PAIR[pair])


def test_an_unknown_team_name_fails_the_run_without_writing(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine
    body = football_data_csv(
        FIXTURES[:30],
        start=START,
        raw_names={"Barcelona": "Barcelona FC"},
    )
    fetcher = FakeFetcher({FD_CURRENT_URL: body})
    expected_rejected = sum(1 for home, away in FIXTURES[:30] if "Barcelona" in (home, away))

    result = run_jobs(
        db.engine, [Job(source="football_data", season=CURRENT)], fetcher, lambda: None
    )[0]

    assert result.status == "failed"
    assert result.rows_read == 30
    assert result.rows_rejected == expected_rejected
    assert result.rows_upserted == 0
    assert result.rows_removed == 0
    assert result.error is not None
    assert "Barcelona FC" in result.error

    assert _count(db, Match) == 0
    assert _count(db, MatchStat) == 0
    assert _count(db, MatchOdd) == 0
    assert _count(db, IngestionRejection) == 0

    runs = _runs(db)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "failed"
    assert run.finished_at is not None
    assert run.rows_read == 30
    assert run.rows_rejected == expected_rejected
    assert run.rows_upserted == 0
    assert run.rows_removed == 0
    assert run.error is not None
    assert "Barcelona FC" in run.error
