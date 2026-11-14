"""Integration tests for the ingestion runner, the status report and the CLI.

These are the M1 acceptance scenarios of the ingestion block, run against a
migrated and seeded database: a full football-data and openfootball load, a
second identical run that changes nothing, a later file that plays ten more
matches, a date move that must reach only the scheduled rows, a fixture list that
omits a stored match, a file with one unusable row, a write that fails after the
matches were inserted, a download that fails, a run lock held elsewhere, a run
interrupted before it finished, a backfill whose seasons ``status`` reports, and
the status report itself: the prefix of every line, and a digest that follows a
change of the matches, the statistics and the odds.

The tests need a live PostgreSQL and are opt-in through ``-m integration``. The
``migrated_engine`` fixture drops and recreates the public schema, so it refuses
a database that is not explicitly disposable and fails instead of skipping when
the ``POSTGRES_*`` variables are unset.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from ingest_support import (
    FakeFetcher,
    aliases_of,
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
from lmi_worker.cli import main
from lmi_worker.http import FetchError
from lmi_worker.ingest import store
from lmi_worker.ingest.pipeline import JobResult, run_jobs
from lmi_worker.ingest.plan import Job, source_url
from lmi_worker.ingest.runs import ABANDONED_ERROR, run_lock, start_run
from lmi_worker.seasons import SeasonCode
from lmi_worker.status import collect_status, format_status
from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import aliased

if TYPE_CHECKING:
    from uuid import UUID

    from conftest import MigratedDatabase

pytestmark = pytest.mark.integration

CURRENT = SeasonCode.parse("2026-27")
PREVIOUS = SeasonCode.parse("2025-26")
SEASON_2005 = SeasonCode.parse("2005-06")
SEASON_2006 = SeasonCode.parse("2006-07")
START = date(2026, 8, 15)
TODAY = date(2026, 11, 14)
"""A date inside the current season, so a backfill and ``--season`` agree with it."""

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
FD_2005_URL = source_url(Job(source="football_data", season=SEASON_2005))
FD_2006_URL = source_url(Job(source="football_data", season=SEASON_2006))


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


def _rejections(db: MigratedDatabase, run_id: UUID) -> list[IngestionRejection]:
    """Return the stored rejections of one run, ordered by row number."""
    with db.factories() as session:
        return list(
            session.scalars(
                select(IngestionRejection)
                .where(IngestionRejection.run_id == run_id)
                .order_by(IngestionRejection.row_number)
            )
        )


def _one_job(db: MigratedDatabase, job: Job, fetcher: FakeFetcher) -> JobResult:
    """Run one job on the test database and return its outcome."""
    return run_jobs(db.engine, [job], fetcher, lambda: None)[0]


@dataclass(frozen=True)
class PairRow:
    """The stored columns of one match that a fixture list may want to change."""

    kickoff_date: date
    status: str
    matchday: int | None
    updated_at: datetime


def _pair_rows(db: MigratedDatabase, code: str) -> dict[tuple[str, str], PairRow]:
    """Return the stored state of one season, keyed by home and away team name."""
    home = aliased(Team)
    away = aliased(Team)
    with db.factories() as session:
        rows = session.execute(
            select(
                home.name,
                away.name,
                Match.kickoff_date,
                Match.status,
                Match.matchday,
                Match.updated_at,
            )
            .join(Season, Season.id == Match.season_id)
            .join(home, home.id == Match.home_team_id)
            .join(away, away.id == Match.away_team_id)
            .where(Season.code == code)
        ).all()
    stored: dict[tuple[str, str], PairRow] = {}
    for row in rows:
        stored[(str(row[0]), str(row[1]))] = PairRow(
            kickoff_date=row[2],
            status=str(row[3]),
            matchday=row[4],
            updated_at=row[5],
        )
    return stored


def _season_id(db: MigratedDatabase, code: str) -> int:
    """Return the id of one seeded season."""
    with db.factories() as session:
        return int(session.scalars(select(Season).where(Season.code == code)).one().id)


def _team_id(db: MigratedDatabase, name: str) -> int:
    """Return the id of one seeded team."""
    with db.factories() as session:
        team_id = session.scalar(select(Team.id).where(Team.name == name))
    assert team_id is not None
    return int(team_id)


def _unlisted_team_name(db: MigratedDatabase) -> str:
    """Return a seeded team that the openfootball fixture list cannot name."""
    listed = set(canonical_teams())
    with db.factories() as session:
        name = session.scalar(select(Team.name).where(Team.name.not_in(listed)).order_by(Team.name))
    assert name is not None
    return str(name)


def _stored_match(db: MigratedDatabase, pair: tuple[str, str], *, played: bool) -> int:
    """Store one match of the current season outside the loader, as a precondition."""
    values: dict[str, object] = {
        "season_id": _season_id(db, CURRENT.code),
        "home_team_id": _team_id(db, pair[0]),
        "away_team_id": _team_id(db, pair[1]),
        "kickoff_date": START,
        "status": "played" if played else "scheduled",
    }
    if played:
        values.update({"home_goals": 2, "away_goals": 1, "result": "H"})
    with db.factories.begin() as session:
        statement = insert(Match).values(**values).returning(Match.id)
        match_id = session.execute(statement).scalar_one()
    return int(match_id)


def _match_id(db: MigratedDatabase, code: str, pair: tuple[str, str]) -> int:
    """Return the id of one stored match."""
    home = aliased(Team)
    away = aliased(Team)
    with db.factories() as session:
        match_id = session.scalar(
            select(Match.id)
            .join(Season, Season.id == Match.season_id)
            .join(home, home.id == Match.home_team_id)
            .join(away, away.id == Match.away_team_id)
            .where(Season.code == code, home.name == pair[0], away.name == pair[1])
        )
    assert match_id is not None
    return int(match_id)


def _set_matchday(db: MigratedDatabase, match_id: int, matchday: int) -> None:
    """Overwrite the stored matchday of one match, as an earlier load would have left it."""
    with db.factories.begin() as session:
        session.execute(update(Match).where(Match.id == match_id).values(matchday=matchday))


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


def test_an_openfootball_date_move_touches_only_the_scheduled_row(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine
    _full_flow(db)
    played = FIXTURES[0]
    scheduled = FIXTURES[40]
    # A played match keeps its matchday even when the fixture list disagrees. The
    # merge may only note that difference, so the row must still hold this value
    # and must not be touched by the load.
    conflicting_matchday = MATCHDAY_BY_PAIR[played] + 1
    _set_matchday(db, _match_id(db, CURRENT.code, played), conflicting_matchday)
    before = _pair_rows(db, CURRENT.code)
    played_before = before[played]
    moved_played = date(2026, 12, 20)
    moved_scheduled = date(2026, 12, 21)
    assert moved_played != played_before.kickoff_date
    assert moved_scheduled != before[scheduled].kickoff_date
    fetcher = FakeFetcher(
        {
            OF_URL: openfootball_json(
                TEAMS,
                start=START,
                date_overrides={played: moved_played, scheduled: moved_scheduled},
            )
        }
    )

    result = _one_job(db, Job(source="openfootball", season=CURRENT), fetcher)

    assert result.status == "succeeded"
    assert result.rows_upserted == 1
    assert result.rows_removed == 0
    assert fetcher.requested == [OF_URL]
    after = _pair_rows(db, CURRENT.code)
    assert len(after) == 380
    assert sum(1 for row in after.values() if row.status == "played") == 30
    assert sum(1 for row in after.values() if row.status == "scheduled") == 350
    played_after = after[played]
    assert played_after.status == "played"
    assert played_after.kickoff_date == played_before.kickoff_date
    assert played_after.matchday == conflicting_matchday
    assert played_after.updated_at == played_before.updated_at
    assert after[scheduled].kickoff_date == moved_scheduled
    assert after[scheduled].status == "scheduled"


def test_a_scheduled_fixture_missing_from_the_snapshot_is_deleted(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine
    stale = _stored_match(db, (_unlisted_team_name(db), TEAMS[0]), played=False)
    fetcher = FakeFetcher({OF_URL: openfootball_json(TEAMS, start=START)})

    result = _one_job(db, Job(source="openfootball", season=CURRENT), fetcher)

    assert result.status == "succeeded"
    assert result.rows_read == 380
    assert result.rows_upserted == 380
    assert result.rows_removed == 1
    with db.factories() as session:
        assert session.get(Match, stale) is None
    rows = _match_rows(db, CURRENT.code)
    assert len(rows) == 380
    assert sum(1 for row in rows if row[2] == "scheduled") == 380
    run = _runs(db)[0]
    assert (run.status, run.rows_upserted, run.rows_removed) == ("succeeded", 380, 1)


def test_a_played_fixture_missing_from_the_snapshot_fails_the_run_without_writing(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine
    stale = _stored_match(db, (_unlisted_team_name(db), TEAMS[0]), played=True)
    fetcher = FakeFetcher({OF_URL: openfootball_json(TEAMS, start=START)})

    result = _one_job(db, Job(source="openfootball", season=CURRENT), fetcher)

    assert result.status == "failed"
    assert result.rows_read == 380
    assert result.rows_upserted == 0
    assert result.rows_removed == 0
    assert result.error is not None
    assert result.error.startswith("SourceConflict:")
    assert str(stale) in result.error
    with db.factories() as session:
        kept = session.get(Match, stale)
    assert kept is not None
    assert (kept.status, kept.home_goals, kept.away_goals, kept.result) == ("played", 2, 1, "H")
    assert len(_match_rows(db, CURRENT.code)) == 1
    assert _count(db, IngestionRejection) == 0
    runs = _runs(db)
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].rows_upserted == 0
    assert runs[0].rows_removed == 0
    assert runs[0].error == result.error


def test_an_inconsistent_full_time_result_fails_the_run_and_keeps_the_raw_row(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine
    broken = 3
    broken_cells = {broken: {"FTR": "A"}}
    body = football_data_csv(FIXTURES[:30], start=START, cell_overrides=broken_cells)
    fetcher = FakeFetcher({FD_CURRENT_URL: body})

    result = _one_job(db, Job(source="football_data", season=CURRENT), fetcher)

    assert result.status == "failed"
    assert result.rows_read == 30
    assert result.rows_rejected == 1
    assert result.rows_upserted == 0
    assert result.rows_removed == 0
    assert result.error is not None
    assert _count(db, Match) == 0
    assert _count(db, MatchStat) == 0
    assert _count(db, MatchOdd) == 0
    assert _count(db, IngestionRejection) == 1
    run = _runs(db)[0]
    assert (run.status, run.rows_read, run.rows_rejected) == ("failed", 30, 1)
    assert result.run_id is not None
    rejections = _rejections(db, result.run_id)
    assert len(rejections) == 1
    rejection = rejections[0]
    assert rejection.row_number == broken + 2
    aliases = aliases_of("football_data")
    home, away = FIXTURES[broken]
    assert rejection.raw["Div"] == "SP1"
    assert rejection.raw["HomeTeam"] == aliases[home]
    assert rejection.raw["AwayTeam"] == aliases[away]
    assert [rejection.raw[column] for column in ("FTHG", "FTAG", "FTR")] == ["2", "1", "A"]
    assert rejection.raw["B365H"] == "2.0625"


def test_an_odds_write_that_raises_rolls_the_whole_match_transaction_back(
    migrated_engine: MigratedDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = migrated_engine
    matches = 30
    reached: set[int] = set()

    def refuse_the_last_odds_write(
        session: object, match_id: int, slot: object, prices: object
    ) -> None:
        reached.add(match_id)
        if len(reached) == matches:
            raise RuntimeError("simulated odds write failure")

    monkeypatch.setattr(store, "_upsert_odds", refuse_the_last_odds_write)
    fetcher = FakeFetcher({FD_CURRENT_URL: _current_csv(matches)})

    result = _one_job(db, Job(source="football_data", season=CURRENT), fetcher)

    assert result.status == "failed"
    assert result.error == "RuntimeError: simulated odds write failure"
    assert len(reached) == matches
    assert (result.rows_upserted, result.rows_removed) == (0, 0)
    assert _count(db, Match) == 0
    assert _count(db, MatchStat) == 0
    assert _count(db, MatchOdd) == 0
    assert _count(db, IngestionRejection) == 0
    run = _runs(db)[0]
    assert run.status == "failed"
    assert run.error == "RuntimeError: simulated odds write failure"
    # The data transaction rolled back, so the run row must not report matches it
    # did not store.
    assert (run.rows_upserted, run.rows_removed) == (0, 0)


def test_a_fetch_error_fails_the_run_and_is_recorded_on_the_run_row(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine
    fetcher = FakeFetcher({FD_CURRENT_URL: FetchError("the source is unreachable")})

    result = _one_job(db, Job(source="football_data", season=CURRENT), fetcher)

    assert result.status == "failed"
    assert result.rows_read == 0
    assert result.error == "FetchError: the source is unreachable"
    run = _runs(db)[0]
    assert run.status == "failed"
    assert run.finished_at is not None
    assert run.error == "FetchError: the source is unreachable"
    assert run.content_sha256 is None
    assert _count(db, Match) == 0


def test_a_locked_job_writes_no_run_and_the_command_exits_one(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine
    job = Job(source="football_data", season=CURRENT)
    fetcher = FakeFetcher({})

    with run_lock(db.engine, job) as acquired:
        assert acquired is True
        results = run_jobs(db.engine, [job], fetcher, lambda: None)
        code = main(
            ["ingest", "--season", CURRENT.code, "--source", "football_data"],
            today=TODAY,
            fetcher=fetcher,
        )

    assert [result.status for result in results] == ["locked"]
    assert results[0].run_id is None
    assert fetcher.requested == []
    assert code == 1
    assert _runs(db) == []
    assert _count(db, Match) == 0


def test_a_running_row_of_the_same_pair_is_marked_abandoned_by_the_next_run(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine
    job = Job(source="football_data", season=CURRENT)
    stale = start_run(db.factories, job, "0")
    fetcher = FakeFetcher({FD_CURRENT_URL: _current_csv(30)})

    result = _one_job(db, job, fetcher)

    assert result.status == "succeeded"
    assert result.run_id is not None
    runs = _runs(db)
    assert len(runs) == 2
    abandoned = next(run for run in runs if run.id == stale)
    assert abandoned.status == "failed"
    assert abandoned.finished_at is not None
    assert abandoned.error == ABANDONED_ERROR
    finished = next(run for run in runs if run.id != stale)
    assert finished.id == result.run_id
    assert finished.status == "succeeded"
    assert finished.error is None
    assert _count(db, Match) == 30


def test_backfill_through_loads_every_season_and_status_reports_them(
    migrated_engine: MigratedDatabase, capsys: pytest.CaptureFixture[str]
) -> None:
    db = migrated_engine
    fetcher = FakeFetcher(
        {
            FD_2005_URL: football_data_csv(FIXTURES[:12], start=date(2005, 8, 15)),
            FD_2006_URL: football_data_csv(FIXTURES[:12], start=date(2006, 8, 15)),
        }
    )

    code = main(["ingest", "--backfill-through", "2006-07"], today=TODAY, fetcher=fetcher)

    assert code == 0
    assert fetcher.requested == [FD_2005_URL, FD_2006_URL]
    runs = _runs(db)
    assert [(run.season_code, run.status) for run in runs] == [
        ("2005-06", "succeeded"),
        ("2006-07", "succeeded"),
    ]
    assert len(_match_rows(db, "2005-06")) == 12
    assert len(_match_rows(db, "2006-07")) == 12

    assert main(["status"]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert "season 2005-06 total=12 played=12 scheduled=0 matchday=0" in printed
    assert "season 2006-07 total=12 played=12 scheduled=0 matchday=0" in printed
    assert any(line.startswith("digest overall ") for line in printed)


def test_a_failed_backfill_season_does_not_stop_the_next_one(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine
    fetcher = FakeFetcher(
        {
            FD_2005_URL: FetchError("the source is unreachable"),
            FD_2006_URL: football_data_csv(FIXTURES[:12], start=date(2006, 8, 15)),
        }
    )

    code = main(["ingest", "--backfill-through", "2006-07"], today=TODAY, fetcher=fetcher)

    assert code == 1
    assert fetcher.requested == [FD_2005_URL, FD_2006_URL]
    runs = _runs(db)
    assert [(run.season_code, run.status) for run in runs] == [
        ("2005-06", "failed"),
        ("2006-07", "succeeded"),
    ]
    assert _match_rows(db, "2005-06") == []
    assert len(_match_rows(db, "2006-07")) == 12


_REPORT_PREFIXES = ("season ", "table ", "run ", "digest ")
"""The only openings a status report line may have."""


def test_the_status_report_labels_every_line_and_keeps_run_facts_on_run_lines(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine
    empty = format_status(collect_status(db.engine)).splitlines()

    assert all(line.startswith(_REPORT_PREFIXES) for line in empty)
    assert any(line.startswith("season ") for line in empty)
    assert any(line.startswith("table ") for line in empty)
    assert not any(line.startswith("run ") for line in empty)
    assert len([line for line in empty if line.startswith("digest ")]) == 4

    _full_flow(db)
    report = collect_status(db.engine)
    lines = format_status(report).splitlines()

    assert report.runs
    assert all(line.startswith(_REPORT_PREFIXES) for line in lines)
    run_lines = [line for line in lines if line.startswith("run ")]
    assert len(run_lines) == len(report.runs)
    for run in report.runs:
        assert any(line.startswith(f"run {run.source} {run.season} ") for line in run_lines)
    # The acceptance comparison filters the lines starting with `run` and compares
    # the rest, so no other line may carry a value that changes between two runs.
    assert [line for line in lines if " upserted=" in line] == run_lines


def test_the_status_digest_tracks_a_change_of_each_stored_component(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine
    _full_flow(db)
    match_id = _match_id(db, CURRENT.code, FIXTURES[0])
    before = collect_status(db.engine)

    with db.factories.begin() as session:
        session.execute(
            update(Match).where(Match.id == match_id).values(kickoff_date=date(2026, 9, 1))
        )
    after_match = collect_status(db.engine)

    assert after_match.digest_matches != before.digest_matches
    assert after_match.digest_stats == before.digest_stats
    assert after_match.digest_odds == before.digest_odds
    assert after_match.digest != before.digest

    with db.factories.begin() as session:
        session.execute(
            update(MatchStat).where(MatchStat.match_id == match_id).values(home_shots=99)
        )
    after_stats = collect_status(db.engine)

    assert after_stats.digest_matches == after_match.digest_matches
    assert after_stats.digest_stats != after_match.digest_stats
    assert after_stats.digest_odds == after_match.digest_odds
    assert after_stats.digest != after_match.digest

    with db.factories.begin() as session:
        session.execute(
            update(MatchOdd)
            .where(MatchOdd.match_id == match_id, MatchOdd.bookmaker == "bet365")
            .values(home=Decimal("2.500"))
        )
    after_odds = collect_status(db.engine)

    assert after_odds.digest_matches == after_stats.digest_matches
    assert after_odds.digest_stats == after_stats.digest_stats
    assert after_odds.digest_odds != after_stats.digest_odds
    assert after_odds.digest != after_stats.digest
