"""Integration tests for the ingestion write primitives.

These tests need a live PostgreSQL and are opt-in, exactly like the core schema
tests: ``make test`` selects ``-m "not integration"`` and skips this module, and
``make test-integration`` starts a throwaway ``lmi-test-db`` and runs
``pytest -m integration`` against it. The ``migrated_engine`` fixture of
``conftest.py`` drops and recreates the ``public`` schema, so it refuses a
database that is not explicitly disposable and fails instead of skipping when the
``POSTGRES_*`` variables are unset.

The plan objects come from the pure merge layer, so the assertions here are about
the database alone: how many rows one plan writes, which columns an update
touches, whether ``updated_at`` moves, whether an upsert replaces a row instead of
adding one, and whether the guarded delete of a played match fails and leaves the
row in place.

The season advisory lock is tested against a second connection, because the point
of the lock is that another writer waits: the holding connection would always
succeed.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from lmi_core.db.models import Match, MatchOdd, MatchStat, Season, Team
from lmi_worker.ingest.merge import (
    FdRecord,
    MatchKey,
    MatchState,
    MergePlan,
    OddsSlot,
    OfRecord,
    Prices,
    StatsState,
    StoredMatch,
)
from lmi_worker.ingest.store import (
    LOCK_CLASS,
    MissingSeason,
    apply_plan,
    get_season,
    load_season,
    lock_season_writes,
)
from sqlalchemy import func, select, text

if TYPE_CHECKING:
    from conftest import MigratedDatabase

pytestmark = pytest.mark.integration

SEASON_CODE = "2026-27"
"""The current season of the fixture; the seed stores 22 seasons up to it."""

BET365_OPENING = OddsSlot(bookmaker="bet365", is_closing=False)
PINNACLE_CLOSING = OddsSlot(bookmaker="pinnacle", is_closing=True)

PRICES = Prices(home=Decimal("1.900"), draw=Decimal("3.400"), away=Decimal("4.200"))
"""A first 1X2 price set; every price is above the CHECK minimum of 1."""

NEW_PRICES = Prices(home=Decimal("2.050"), draw=Decimal("3.200"), away=Decimal("3.800"))
"""A second price set, used to prove that an upsert replaces the stored price."""

DEFAULT_STATS = StatsState(
    home_shots=10,
    away_shots=8,
    home_shots_on_target=5,
    away_shots_on_target=3,
    home_yellow=1,
    away_yellow=2,
    home_xg=Decimal("1.23"),
    away_xg=Decimal("0.87"),
)
"""The statistics of the played record; the counters left out stay NULL."""


def _season_and_teams(migrated: MigratedDatabase, *, teams: int = 2) -> tuple[int, list[int]]:
    """Return the id of the current season and the ids of ``teams`` seeded teams."""
    with migrated.factories() as session:
        season = get_season(session, SEASON_CODE)
        team_ids = [
            team.id for team in session.scalars(select(Team).order_by(Team.id).limit(teams))
        ]
    if len(team_ids) != teams:
        pytest.fail(f"the seed has fewer than {teams} teams")
    return season.id, team_ids


def _fd_record(
    home_team_id: int,
    away_team_id: int,
    *,
    home_goals: int = 2,
    away_goals: int = 1,
    kickoff_time: time | None = time(21, 0),
    stats: StatsState = DEFAULT_STATS,
    odds: Mapping[OddsSlot, Prices] | None = None,
) -> FdRecord:
    """Return one played football-data record; the result follows the score."""
    if home_goals > away_goals:
        result = "H"
    elif home_goals == away_goals:
        result = "D"
    else:
        result = "A"
    return FdRecord(
        key=MatchKey(home_team_id=home_team_id, away_team_id=away_team_id),
        state=MatchState(
            kickoff_date=date(2026, 8, 22),
            kickoff_time=kickoff_time,
            matchday=None,
            status="played",
            home_goals=home_goals,
            away_goals=away_goals,
            ht_home_goals=None,
            ht_away_goals=None,
            result=result,
        ),
        stats=stats,
        odds={} if odds is None else dict(odds),
    )


def _of_record(home_team_id: int, away_team_id: int) -> OfRecord:
    """Return one scheduled openfootball fixture."""
    return OfRecord(
        key=MatchKey(home_team_id=home_team_id, away_team_id=away_team_id),
        kickoff_date=date(2026, 9, 13),
        matchday=3,
    )


def _apply(migrated: MigratedDatabase, season_id: int, plan: MergePlan) -> None:
    """Apply ``plan`` in one committed transaction, like the loader does."""
    with migrated.factories.begin() as session:
        apply_plan(session, season_id, plan)


def _insert(migrated: MigratedDatabase, season_id: int, record: FdRecord | OfRecord) -> int:
    """Insert one record and return the id the database gave the row."""
    _apply(migrated, season_id, MergePlan(inserts=[record]))
    return _load(migrated, season_id)[record.key].id


def _load(migrated: MigratedDatabase, season_id: int) -> dict[MatchKey, StoredMatch]:
    """Return the stored season, read in a fresh session."""
    with migrated.factories() as session:
        return load_season(session, season_id)


def _count(
    migrated: MigratedDatabase, model: type[Match] | type[MatchStat] | type[MatchOdd]
) -> int:
    """Return the number of rows of one table."""
    with migrated.factories() as session:
        total = session.scalar(select(func.count()).select_from(model))
    return int(total or 0)


def _updated_at(migrated: MigratedDatabase, match_id: int) -> datetime:
    """Return the ``updated_at`` stamp of one match."""
    with migrated.factories() as session:
        stamp = session.scalars(select(Match.updated_at).where(Match.id == match_id)).one()
    return stamp


def _markets(migrated: MigratedDatabase) -> list[str]:
    """Return the market of every stored odds row, oldest row first."""
    with migrated.factories() as session:
        return [
            str(market) for market in session.scalars(select(MatchOdd.market).order_by(MatchOdd.id))
        ]


def test_insert_writes_the_match_its_stats_and_its_odds(
    migrated_engine: MigratedDatabase,
) -> None:
    season_id, team_ids = _season_and_teams(migrated_engine)
    record = _fd_record(
        team_ids[0], team_ids[1], odds={BET365_OPENING: PRICES, PINNACLE_CLOSING: PRICES}
    )

    match_id = _insert(migrated_engine, season_id, record)

    assert _count(migrated_engine, Match) == 1
    assert _count(migrated_engine, MatchStat) == 1
    assert _count(migrated_engine, MatchOdd) == 2
    stored = _load(migrated_engine, season_id)[record.key]
    assert stored.id == match_id
    assert stored.state == record.state
    assert stored.stats == record.stats
    assert dict(stored.odds) == dict(record.odds)
    assert _markets(migrated_engine) == ["1x2", "1x2"]
    assert _updated_at(migrated_engine, match_id) is not None


def test_an_empty_plan_writes_nothing(migrated_engine: MigratedDatabase) -> None:
    season_id, team_ids = _season_and_teams(migrated_engine)
    record = _fd_record(team_ids[0], team_ids[1], odds={BET365_OPENING: PRICES})
    match_id = _insert(migrated_engine, season_id, record)
    stamp = _updated_at(migrated_engine, match_id)

    _apply(migrated_engine, season_id, MergePlan())

    assert _count(migrated_engine, Match) == 1
    assert _count(migrated_engine, MatchStat) == 1
    assert _count(migrated_engine, MatchOdd) == 1
    assert _updated_at(migrated_engine, match_id) == stamp
    assert set(_load(migrated_engine, season_id)) == {record.key}


def test_update_changes_only_the_planned_columns_and_moves_updated_at(
    migrated_engine: MigratedDatabase,
) -> None:
    season_id, team_ids = _season_and_teams(migrated_engine)
    record = _fd_record(team_ids[0], team_ids[1], home_goals=1, away_goals=0)
    match_id = _insert(migrated_engine, season_id, record)
    before = _updated_at(migrated_engine, match_id)

    _apply(
        migrated_engine,
        season_id,
        MergePlan(updates=[(match_id, {"home_goals": 2, "away_goals": 2, "result": "D"})]),
    )

    after = _load(migrated_engine, season_id)[record.key]
    assert after.state.home_goals == 2
    assert after.state.away_goals == 2
    assert after.state.result == "D"
    assert after.state.kickoff_date == record.state.kickoff_date
    assert after.state.kickoff_time == record.state.kickoff_time
    assert after.state.status == record.state.status
    assert after.state.matchday is None
    assert after.stats == record.stats
    assert _updated_at(migrated_engine, match_id) > before


def test_stats_upsert_replaces_the_row(migrated_engine: MigratedDatabase) -> None:
    season_id, team_ids = _season_and_teams(migrated_engine)
    record = _fd_record(team_ids[0], team_ids[1])
    match_id = _insert(migrated_engine, season_id, record)
    replacement = StatsState(home_shots=12, home_xg=Decimal("1.85"))

    _apply(migrated_engine, season_id, MergePlan(stats=[(match_id, replacement)]))

    assert _count(migrated_engine, MatchStat) == 1
    stored = _load(migrated_engine, season_id)[record.key]
    assert stored.stats == replacement


def test_odds_upsert_changes_the_price_and_the_delete_drops_only_that_slot(
    migrated_engine: MigratedDatabase,
) -> None:
    season_id, team_ids = _season_and_teams(migrated_engine)
    record = _fd_record(
        team_ids[0], team_ids[1], odds={BET365_OPENING: PRICES, PINNACLE_CLOSING: PRICES}
    )
    match_id = _insert(migrated_engine, season_id, record)

    _apply(
        migrated_engine,
        season_id,
        MergePlan(
            odds_upserts=[(match_id, BET365_OPENING, NEW_PRICES)],
            odds_deletes=[(match_id, PINNACLE_CLOSING)],
        ),
    )

    assert _count(migrated_engine, MatchOdd) == 1
    stored = _load(migrated_engine, season_id)[record.key]
    assert dict(stored.odds) == {BET365_OPENING: NEW_PRICES}


def test_deleting_a_played_match_raises_and_leaves_the_row(
    migrated_engine: MigratedDatabase,
) -> None:
    season_id, team_ids = _season_and_teams(migrated_engine)
    record = _fd_record(team_ids[0], team_ids[1])
    match_id = _insert(migrated_engine, season_id, record)

    with pytest.raises(RuntimeError, match="status = 'scheduled'"):
        _apply(migrated_engine, season_id, MergePlan(deletes=[match_id]))

    assert _count(migrated_engine, Match) == 1
    assert _load(migrated_engine, season_id)[record.key].id == match_id
    assert _count(migrated_engine, MatchStat) == 1


def test_load_season_returns_stats_and_odds_and_the_scheduled_rows(
    migrated_engine: MigratedDatabase,
) -> None:
    season_id, team_ids = _season_and_teams(migrated_engine, teams=4)
    played = _fd_record(team_ids[0], team_ids[1], odds={BET365_OPENING: PRICES})
    scheduled = _of_record(team_ids[2], team_ids[3])

    _apply(migrated_engine, season_id, MergePlan(inserts=[played, scheduled]))

    stored = _load(migrated_engine, season_id)
    assert set(stored) == {played.key, scheduled.key}
    assert stored[played.key].state == played.state
    assert stored[played.key].stats == played.stats
    assert dict(stored[played.key].odds) == dict(played.odds)

    fixture = stored[scheduled.key]
    assert fixture.state.status == "scheduled"
    assert fixture.state.kickoff_date == scheduled.kickoff_date
    assert fixture.state.matchday == scheduled.matchday
    assert fixture.state.kickoff_time is None
    assert fixture.state.home_goals is None
    assert fixture.state.result is None
    assert fixture.stats is None
    assert dict(fixture.odds) == {}


def test_get_season_raises_missing_season_for_an_unseeded_code(
    migrated_engine: MigratedDatabase,
) -> None:
    with migrated_engine.factories() as session, pytest.raises(MissingSeason) as missing:
        get_season(session, "2004-05")

    assert "2004-05" in str(missing.value)
    assert "2005-06..2026-27" in str(missing.value)


def test_get_season_rejects_a_contradictory_football_data_code(
    migrated_engine: MigratedDatabase,
) -> None:
    with migrated_engine.factories.begin() as session:
        session.add(Season(code="2004-05", start_year=2004, fd_code="9900"))

    with migrated_engine.factories() as session, pytest.raises(ValueError) as rejected:
        get_season(session, "2004-05")

    assert "0405" in str(rejected.value)


def test_season_lock_blocks_a_second_writer_until_the_transaction_ends(
    migrated_engine: MigratedDatabase,
) -> None:
    with migrated_engine.factories() as session:
        start_year = get_season(session, SEASON_CODE).start_year

        lock_season_writes(session, start_year)

        with migrated_engine.engine.connect() as other:
            blocked = other.execute(
                text("SELECT pg_try_advisory_xact_lock(:lock_class, :key)"),
                {"lock_class": LOCK_CLASS, "key": start_year},
            ).scalar_one()
            assert blocked is False
            other.rollback()

        session.rollback()

    with migrated_engine.engine.connect() as other:
        acquired = other.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_class, :key)"),
            {"lock_class": LOCK_CLASS, "key": start_year},
        ).scalar_one()
        assert acquired is True
        other.rollback()
