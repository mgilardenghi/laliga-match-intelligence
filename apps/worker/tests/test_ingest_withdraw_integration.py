"""Integration tests for the removal step of the ingestion runners.

A match a loader no longer sees is a removal candidate, and the store decides
what removal means per candidate. These scenarios run against a migrated and
seeded database and check the branch the live-prediction schema adds: a candidate that carries
a stored prediction is withdrawn instead of deleted, a candidate without one is
still deleted, and a stored ``withdrawn`` match that a later fixture list carries
again is restored in the statement that fixes its date.

The prediction rows need the ``predictions_guard_insert`` trigger to accept them,
so every prediction here uses :mod:`lmi_core.clock` for its as-of date and a match
whose kickoff date is strictly after that date. The trigger is never disabled.

The third scenario, a candidate without a prediction that is still deleted, is
already the acceptance test ``test_a_scheduled_fixture_missing_from_the_snapshot_is_deleted``
in ``test_ingest_integration.py``; that test asserts the new ``rows_withdrawn``
counter instead of duplicating the scenario here.

The tests need a live PostgreSQL and are opt-in through ``-m integration``,
exactly like ``test_ingest_integration.py``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from ingest_support import FakeFetcher, canonical_teams, openfootball_json
from lmi_core.clock import madrid_today
from lmi_core.db.models import IngestionRun, Match, Prediction, PredictionRun, Season, Team
from lmi_worker.ingest.pipeline import JobResult, run_jobs
from lmi_worker.ingest.plan import Job, source_url
from lmi_worker.seasons import SeasonCode
from sqlalchemy import func, insert, select, update

if TYPE_CHECKING:
    from conftest import MigratedDatabase

pytestmark = pytest.mark.integration

CURRENT = SeasonCode.parse("2026-27")
START = date(2026, 8, 15)
"""The first matchday date of the fixture list, as in the ingestion tests."""

TEAMS = canonical_teams()
OF_JOB = Job(source="openfootball", season=CURRENT)
OF_URL = source_url(OF_JOB)


def _season_id(db: MigratedDatabase) -> int:
    """Return the id of the current season."""
    with db.factories() as session:
        return int(session.scalars(select(Season).where(Season.code == CURRENT.code)).one().id)


def _team_id(db: MigratedDatabase, name: str) -> int:
    """Return the id of one seeded team."""
    with db.factories() as session:
        team_id = session.scalar(select(Team.id).where(Team.name == name))
    assert team_id is not None
    return int(team_id)


def _unlisted_team_name(db: MigratedDatabase) -> str:
    """Return a seeded team that the openfootball fixture list cannot name."""
    listed = set(TEAMS)
    with db.factories() as session:
        name = session.scalar(select(Team.name).where(Team.name.not_in(listed)).order_by(Team.name))
    assert name is not None
    return str(name)


def _store_scheduled_match(
    db: MigratedDatabase, pair: tuple[str, str], *, kickoff_date: date
) -> int:
    """Store one scheduled match outside the loader, as a precondition."""
    with db.factories.begin() as session:
        match_id = session.execute(
            insert(Match)
            .values(
                season_id=_season_id(db),
                home_team_id=_team_id(db, pair[0]),
                away_team_id=_team_id(db, pair[1]),
                kickoff_date=kickoff_date,
                status="scheduled",
            )
            .returning(Match.id)
        ).scalar_one()
    return int(match_id)


def _store_prediction(db: MigratedDatabase, match_id: int, *, as_of_date: date) -> None:
    """Store one prediction run and one prediction, as the live pass would.

    The match must already be committed, because the insert trigger reads its
    kickoff date and status, and the kickoff date must be strictly after
    ``as_of_date``, because the trigger refuses a prediction that could see a
    match on or after its own Madrid midnight.
    """
    run_id = uuid4()
    with db.factories.begin() as session:
        session.execute(
            insert(PredictionRun).values(
                id=run_id,
                as_of_date=as_of_date,
                status="running",
                trigger="cli",
                horizon_days=7,
                code_version="test",
            )
        )
        session.execute(
            insert(Prediction).values(
                run_id=run_id,
                match_id=match_id,
                model="home_win",
                model_version="test",
                model_config={},
                as_of_date=as_of_date,
                p_home=0.5,
                p_draw=0.3,
                p_away=0.2,
            )
        )


def _match_id(db: MigratedDatabase, pair: tuple[str, str]) -> int:
    """Return the id of one stored match of the current season."""
    with db.factories() as session:
        match_id = session.scalar(
            select(Match.id)
            .join(Season, Season.id == Match.season_id)
            .where(
                Season.code == CURRENT.code,
                Match.home_team_id == _team_id(db, pair[0]),
                Match.away_team_id == _team_id(db, pair[1]),
            )
        )
    assert match_id is not None
    return int(match_id)


def _removal_state(db: MigratedDatabase, match_id: int) -> tuple[str, datetime | None]:
    """Return the status and the withdrawal stamp of one stored match."""
    with db.factories() as session:
        row = session.execute(
            select(Match.status, Match.withdrawn_at).where(Match.id == match_id)
        ).one()
    return str(row[0]), row[1]


def _prediction_count(db: MigratedDatabase, match_id: int) -> int:
    """Return the number of stored predictions that reference one match."""
    with db.factories() as session:
        count = session.scalar(
            select(func.count()).select_from(Prediction).where(Prediction.match_id == match_id)
        )
    return int(count or 0)


def _run(db: MigratedDatabase, run_id: UUID) -> IngestionRun:
    """Return one stored run row."""
    with db.factories() as session:
        run = session.get(IngestionRun, run_id)
    assert run is not None
    return run


def _withdraw(db: MigratedDatabase, match_id: int) -> None:
    """Withdraw one stored match outside the loader, as an earlier run would have."""
    with db.factories.begin() as session:
        session.execute(
            update(Match)
            .where(Match.id == match_id)
            .values(status="withdrawn", withdrawn_at=func.now())
        )


def _load(db: MigratedDatabase) -> JobResult:
    """Run the openfootball job against the complete fixture list."""
    fetcher = FakeFetcher({OF_URL: openfootball_json(TEAMS, start=START)})
    return run_jobs(db.engine, [OF_JOB], fetcher, lambda: None)[0]


def test_a_removal_candidate_with_a_stored_prediction_is_withdrawn(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine
    today = madrid_today()
    pair = (_unlisted_team_name(db), TEAMS[0])
    match_id = _store_scheduled_match(db, pair, kickoff_date=today + timedelta(days=5))
    _store_prediction(db, match_id, as_of_date=today)

    result = _load(db)

    assert result.status == "succeeded"
    assert result.rows_read == 380
    assert result.rows_upserted == 380
    assert (result.rows_withdrawn, result.rows_removed) == (1, 0)
    status, withdrawn_at = _removal_state(db, match_id)
    assert status == "withdrawn"
    assert withdrawn_at is not None
    assert _prediction_count(db, match_id) == 1
    assert result.run_id is not None
    run = _run(db, result.run_id)
    assert run.status == "succeeded"
    assert (run.rows_withdrawn, run.rows_removed) == (1, 0)


def test_a_withdrawn_fixture_the_snapshot_lists_again_is_restored(
    migrated_engine: MigratedDatabase,
) -> None:
    db = migrated_engine
    first = _load(db)
    assert first.status == "succeeded"
    pair = (TEAMS[0], TEAMS[1])
    match_id = _match_id(db, pair)
    _withdraw(db, match_id)

    second = _load(db)

    assert second.status == "succeeded"
    assert second.rows_upserted == 1
    assert (second.rows_withdrawn, second.rows_removed) == (0, 0)
    assert _removal_state(db, match_id) == ("scheduled", None)
    assert second.run_id is not None
    restored_run = _run(db, second.run_id)
    assert (restored_run.rows_upserted, restored_run.rows_withdrawn) == (1, 0)

    third = _load(db)

    assert third.status == "succeeded"
    assert (third.rows_upserted, third.rows_removed, third.rows_withdrawn) == (0, 0, 0)
    assert _removal_state(db, match_id) == ("scheduled", None)
