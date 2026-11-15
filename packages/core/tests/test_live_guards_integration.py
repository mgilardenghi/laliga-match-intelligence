"""Integration tests for the live-prediction guards of revision ``0003``.

The revision installs two trigger functions that decide which live row the
database accepts: ``lmi_predictions_guard_insert`` stamps ``created_at`` and
refuses a prediction that could see the future, and ``lmi_prediction_runs_guard``
allows only the transition of a running prediction run out of ``running``. The
``predictions_immutable`` and ``*_no_truncate`` triggers refuse every other
write. These tests drive those guards against a throwaway PostgreSQL migrated to
``head``, and they prove that the SQL boundary of a Madrid day names the same
instant as :func:`lmi_core.clock.kickoff_boundary`.

The structural assertions of the migration itself live in
``test_migration_m4_live.py``; this module observes behavior only. Every date a
scenario stores comes from one PostgreSQL query, so a run that crosses Madrid
midnight cannot split a fixture date from its as-of date.

Like ``tests/test_db_integration.py`` these tests need a live PostgreSQL and are
opt-in: ``make test`` deselects them and ``make test-integration`` starts a
throwaway container. The fixture fails rather than skips when the environment is
not a disposable database, because a missing database is a configuration error,
not a missing capability. There is no shared ``conftest.py`` in this package, so
the fixture lives here.
"""

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import cast

import alembic.command
import pytest
from lmi_core.clock import kickoff_boundary
from lmi_core.config import Settings
from lmi_core.db.alembic_support import alembic_config
from lmi_core.db.session import make_engine
from lmi_core.db.testing import require_disposable_database
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, ProgrammingError

pytestmark = pytest.mark.integration

POSTGRES_ENVIRONMENT_VARIABLES = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)
"""The variables the fixture requires; a missing one is a hard failure."""

_INSERT_SEASON = """
INSERT INTO seasons (code, start_year, fd_code) VALUES ('2024-25', 2024, '2024-2025')
RETURNING id
"""
"""One season row, so a match row has a valid ``season_id``."""

_INSERT_TEAM = "INSERT INTO teams (name) VALUES (:name) RETURNING id"
"""One team row; the fixture stores two, directly with SQL."""

_SELECT_MADRID_DATES = """
SELECT today - 1 AS yesterday, today, today + 1 AS tomorrow
FROM (SELECT (now() AT TIME ZONE 'Europe/Madrid')::date AS today) AS dates
"""
"""Yesterday, today and tomorrow on the Madrid calendar, from one statement.

One statement is the point: two calls could straddle Madrid midnight and give a
scenario a today that is not the today of its tomorrow.
"""

_INSERT_MATCH = """
INSERT INTO matches (
    season_id, kickoff_date, home_team_id, away_team_id, status,
    home_goals, away_goals, result, withdrawn_at
) VALUES (
    :season_id, :kickoff_date, :home_team_id, :away_team_id, :status,
    CAST(:home_goals AS integer), CAST(:away_goals AS integer), CAST(:result AS text),
    CASE WHEN :status = 'withdrawn' THEN now() ELSE NULL END
)
RETURNING id
"""
"""One match row; the withdrawn stamp comes from the database clock."""

_INSERT_PREDICTION_RUN = """
INSERT INTO prediction_runs (id, as_of_date, status, trigger, horizon_days, code_version)
VALUES (:id, :as_of_date, 'running', 'cli', 7, 'test')
RETURNING id
"""
"""A running prediction run, the only state from which the worker starts."""

_INSERT_PREDICTION = """
INSERT INTO predictions (
    run_id, match_id, model, model_version, model_config, as_of_date, created_at,
    p_home, p_draw, p_away
) VALUES (
    :run_id, :match_id, :model, :model_version, '{}'::jsonb, :as_of_date,
    COALESCE(CAST(:created_at AS timestamptz), now()),
    :p_home, :p_draw, :p_away
)
RETURNING id, created_at
"""
"""One prediction row; the insert trigger overwrites ``created_at`` anyway."""

_SELECT_PROBABILITIES = "SELECT p_home, p_draw, p_away FROM predictions WHERE id = :id"
"""The probability triple of one stored prediction, for the immutability checks."""

_SELECT_TIMESTAMP_ONE_DAY_AGO = "SELECT now() - interval '1 day'"
"""A past ``created_at`` the test sends, so the trigger's overwrite is visible."""

_SELECT_SQL_BOUNDARY = """
SELECT (CAST(:day AS date))::timestamp AT TIME ZONE 'Europe/Madrid' AS boundary
"""
"""The Madrid midnight of one day as PostgreSQL computes it, as an instant.

``timezone('Europe/Madrid', ...)`` is deliberately not used: PostgreSQL carries
its own copy of the zone database, and the point of the test is that its answer
agrees with the one :mod:`lmi_core.clock` gives.
"""


@dataclass(frozen=True)
class Database:
    """The throwaway database, its settings, and the fixture rows."""

    engine: Engine
    settings: Settings
    season_id: int
    home_team_id: int
    away_team_id: int


@dataclass(frozen=True)
class StoredPrediction:
    """The identity and the database-stamped creation instant of a stored row."""

    id: int
    created_at: datetime


@pytest.fixture
def database() -> Iterator[Database]:
    """Yield a migrated database with one season and two teams.

    Every test starts from ``DROP SCHEMA public CASCADE`` plus ``CREATE SCHEMA
    public`` and then upgrades to ``head``, so the tests do not depend on each
    other or on their order.

    Raises:
        pytest.fail: the destructive-test guard refused the environment, or one of
            the ``POSTGRES_*`` variables is missing. The message names the
            variable to set and never a value.
    """
    try:
        require_disposable_database()
    except RuntimeError as refusal:
        pytest.fail(str(refusal), pytrace=False)

    missing = [name for name in POSTGRES_ENVIRONMENT_VARIABLES if not os.environ.get(name)]
    if missing:
        pytest.fail(
            "the integration tests need a live PostgreSQL: "
            + ", ".join(missing)
            + " is not set; run `make test-integration`",
            pytrace=False,
        )

    settings = Settings()
    engine = make_engine(settings)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        alembic.command.upgrade(alembic_config(settings), "head")
        with engine.begin() as connection:
            season_id = int(connection.execute(text(_INSERT_SEASON)).scalar_one())
            home_team_id = int(
                connection.execute(text(_INSERT_TEAM), {"name": "Real Test"}).scalar_one()
            )
            away_team_id = int(
                connection.execute(text(_INSERT_TEAM), {"name": "Test United"}).scalar_one()
            )
        yield Database(
            engine=engine,
            settings=settings,
            season_id=season_id,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
        )
    finally:
        engine.dispose()


def _madrid_dates(engine: Engine) -> tuple[date, date, date]:
    """Return today, yesterday and tomorrow on the Madrid calendar, from one query."""
    with engine.connect() as connection:
        row = connection.execute(text(_SELECT_MADRID_DATES)).one()
    return (
        cast(date, row.today),
        cast(date, row.yesterday),
        cast(date, row.tomorrow),
    )


def _store_match(
    engine: Engine,
    *,
    season_id: int,
    home_team_id: int,
    away_team_id: int,
    kickoff_date: date,
    status: str = "scheduled",
    home_goals: int | None = None,
    away_goals: int | None = None,
    result: str | None = None,
) -> int:
    """Store one match and return its id."""
    with engine.begin() as connection:
        match_id = connection.execute(
            text(_INSERT_MATCH),
            {
                "season_id": season_id,
                "kickoff_date": kickoff_date,
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
                "status": status,
                "home_goals": home_goals,
                "away_goals": away_goals,
                "result": result,
            },
        ).scalar_one()
    return int(match_id)


def _store_prediction_run(engine: Engine, *, as_of_date: date) -> uuid.UUID:
    """Store one running prediction run and return its id."""
    run_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(_INSERT_PREDICTION_RUN),
            {"id": run_id, "as_of_date": as_of_date},
        )
    return run_id


def _store_prediction(
    engine: Engine,
    *,
    run_id: uuid.UUID,
    match_id: int,
    as_of_date: date,
    created_at: datetime | None = None,
    model: str = "home_win",
    model_version: str = "test",
    p_home: float = 0.5,
    p_draw: float = 0.3,
    p_away: float = 0.2,
) -> StoredPrediction:
    """Store one prediction and return its id and its database-stamped ``created_at``.

    The run must already be committed and the match must satisfy the insert
    trigger: a rejected insert propagates and rolls back its own transaction.
    """
    with engine.begin() as connection:
        row = connection.execute(
            text(_INSERT_PREDICTION),
            {
                "run_id": run_id,
                "match_id": match_id,
                "model": model,
                "model_version": model_version,
                "as_of_date": as_of_date,
                "created_at": created_at,
                "p_home": p_home,
                "p_draw": p_draw,
                "p_away": p_away,
            },
        ).one()
    return StoredPrediction(id=int(row.id), created_at=cast(datetime, row.created_at))


def _stored_probabilities(engine: Engine, prediction_id: int) -> tuple[float, float, float]:
    """Return the probability triple of one stored prediction."""
    with engine.connect() as connection:
        row = connection.execute(text(_SELECT_PROBABILITIES), {"id": prediction_id}).one()
    return (float(row.p_home), float(row.p_draw), float(row.p_away))


def _stored_sql_boundary(engine: Engine, day: date) -> datetime:
    """Return the Madrid midnight of ``day`` as PostgreSQL computes it."""
    with engine.connect() as connection:
        boundary = connection.execute(text(_SELECT_SQL_BOUNDARY), {"day": day}).scalar_one()
    return cast(datetime, boundary)


def test_a_prediction_for_a_match_dated_today_is_rejected(
    database: Database,
) -> None:
    """L1: the kickoff-date boundary rejects today and accepts tomorrow."""
    today, _yesterday, tomorrow = _madrid_dates(database.engine)
    run_id = _store_prediction_run(database.engine, as_of_date=today)

    today_match_id = _store_match(
        database.engine,
        season_id=database.season_id,
        home_team_id=database.home_team_id,
        away_team_id=database.away_team_id,
        kickoff_date=today,
    )

    with pytest.raises(ProgrammingError) as rejected:
        _store_prediction(
            database.engine,
            run_id=run_id,
            match_id=today_match_id,
            as_of_date=today,
        )

    assert "lmi:" in str(rejected.value)
    assert "kickoff date boundary" in str(rejected.value)

    tomorrow_match_id = _store_match(
        database.engine,
        season_id=database.season_id,
        home_team_id=database.away_team_id,
        away_team_id=database.home_team_id,
        kickoff_date=tomorrow,
    )
    stored = _store_prediction(
        database.engine,
        run_id=run_id,
        match_id=tomorrow_match_id,
        as_of_date=today,
    )

    assert stored.id > 0
    with database.engine.connect() as connection:
        stored_count = connection.execute(
            text("SELECT count(*) FROM predictions WHERE match_id = :match_id"),
            {"match_id": tomorrow_match_id},
        ).scalar_one()
    assert stored_count == 1


def test_as_of_date_and_creation_stamp_are_enforced(database: Database) -> None:
    """L2: yesterday is not the creation date, and the trigger stamps now()."""
    today, yesterday, tomorrow = _madrid_dates(database.engine)
    match_id = _store_match(
        database.engine,
        season_id=database.season_id,
        home_team_id=database.home_team_id,
        away_team_id=database.away_team_id,
        kickoff_date=tomorrow,
    )

    stale_run_id = _store_prediction_run(database.engine, as_of_date=yesterday)
    with pytest.raises(ProgrammingError) as rejected:
        _store_prediction(
            database.engine,
            run_id=stale_run_id,
            match_id=match_id,
            as_of_date=yesterday,
        )

    assert "lmi:" in str(rejected.value)
    assert "as_of_date must be the Madrid date" in str(rejected.value)

    run_id = _store_prediction_run(database.engine, as_of_date=today)
    with database.engine.connect() as connection:
        sent_created_at = cast(
            datetime,
            connection.execute(text(_SELECT_TIMESTAMP_ONE_DAY_AGO)).scalar_one(),
        )

    stored = _store_prediction(
        database.engine,
        run_id=run_id,
        match_id=match_id,
        as_of_date=today,
        created_at=sent_created_at,
    )

    assert stored.created_at > sent_created_at


def test_stored_predictions_are_immutable_and_runs_allow_one_transition(
    database: Database,
) -> None:
    """L3: predictions reject every write; a run allows only its one transition."""
    today, _yesterday, tomorrow = _madrid_dates(database.engine)
    match_id = _store_match(
        database.engine,
        season_id=database.season_id,
        home_team_id=database.home_team_id,
        away_team_id=database.away_team_id,
        kickoff_date=tomorrow,
    )
    run_id = _store_prediction_run(database.engine, as_of_date=today)
    stored = _store_prediction(
        database.engine,
        run_id=run_id,
        match_id=match_id,
        as_of_date=today,
    )
    expected_probabilities = (0.5, 0.3, 0.2)

    with pytest.raises(ProgrammingError) as update_rejected, database.engine.begin() as connection:
        connection.execute(
            text("UPDATE predictions SET p_home = 0.4 WHERE id = :id"),
            {"id": stored.id},
        )
    assert "lmi:" in str(update_rejected.value)
    assert _stored_probabilities(database.engine, stored.id) == pytest.approx(
        expected_probabilities
    )

    with pytest.raises(ProgrammingError) as delete_rejected, database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM predictions WHERE id = :id"),
            {"id": stored.id},
        )
    assert "lmi:" in str(delete_rejected.value)
    assert _stored_probabilities(database.engine, stored.id) == pytest.approx(
        expected_probabilities
    )

    with (
        pytest.raises(ProgrammingError) as truncate_rejected,
        database.engine.begin() as connection,
    ):
        connection.execute(text("TRUNCATE predictions"))
    assert "lmi:" in str(truncate_rejected.value)
    assert _stored_probabilities(database.engine, stored.id) == pytest.approx(
        expected_probabilities
    )

    with (
        pytest.raises(ProgrammingError) as run_delete_rejected,
        database.engine.begin() as connection,
    ):
        connection.execute(
            text("DELETE FROM prediction_runs WHERE id = :run_id"),
            {"run_id": run_id},
        )
    assert "lmi:" in str(run_delete_rejected.value)

    with database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE prediction_runs SET status = 'succeeded', finished_at = now() "
                "WHERE id = :run_id"
            ),
            {"run_id": run_id},
        )

    with (
        pytest.raises(ProgrammingError) as run_update_rejected,
        database.engine.begin() as connection,
    ):
        connection.execute(
            text("UPDATE prediction_runs SET status = 'failed' WHERE id = :run_id"),
            {"run_id": run_id},
        )
    assert "lmi:" in str(run_update_rejected.value)


def test_a_match_that_is_not_scheduled_is_rejected(database: Database) -> None:
    """L4: a played and a withdrawn match both name their status in the refusal."""
    today, yesterday, tomorrow = _madrid_dates(database.engine)
    run_id = _store_prediction_run(database.engine, as_of_date=today)

    played_match_id = _store_match(
        database.engine,
        season_id=database.season_id,
        home_team_id=database.home_team_id,
        away_team_id=database.away_team_id,
        kickoff_date=yesterday,
        status="played",
        home_goals=2,
        away_goals=1,
        result="H",
    )
    with pytest.raises(ProgrammingError) as played_rejected:
        _store_prediction(
            database.engine,
            run_id=run_id,
            match_id=played_match_id,
            as_of_date=today,
        )
    assert "lmi:" in str(played_rejected.value)
    assert "played" in str(played_rejected.value)

    withdrawn_match_id = _store_match(
        database.engine,
        season_id=database.season_id,
        home_team_id=database.away_team_id,
        away_team_id=database.home_team_id,
        kickoff_date=tomorrow,
        status="withdrawn",
    )
    with pytest.raises(ProgrammingError) as withdrawn_rejected:
        _store_prediction(
            database.engine,
            run_id=run_id,
            match_id=withdrawn_match_id,
            as_of_date=today,
        )
    assert "lmi:" in str(withdrawn_rejected.value)
    assert "withdrawn" in str(withdrawn_rejected.value)


def test_a_match_with_a_stored_prediction_cannot_be_deleted(database: Database) -> None:
    """L5: the prediction reference is ON DELETE RESTRICT."""
    today, _yesterday, tomorrow = _madrid_dates(database.engine)
    match_id = _store_match(
        database.engine,
        season_id=database.season_id,
        home_team_id=database.home_team_id,
        away_team_id=database.away_team_id,
        kickoff_date=tomorrow,
    )
    run_id = _store_prediction_run(database.engine, as_of_date=today)
    stored = _store_prediction(
        database.engine,
        run_id=run_id,
        match_id=match_id,
        as_of_date=today,
    )

    with pytest.raises(IntegrityError) as rejected, database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM matches WHERE id = :match_id"),
            {"match_id": match_id},
        )

    assert "fk_predictions_match_id_matches" in str(rejected.value)

    with database.engine.connect() as connection:
        match_count = connection.execute(
            text("SELECT count(*) FROM matches WHERE id = :match_id"),
            {"match_id": match_id},
        ).scalar_one()
        prediction_count = connection.execute(
            text("SELECT count(*) FROM predictions WHERE id = :id"),
            {"id": stored.id},
        ).scalar_one()
    assert match_count == 1
    assert prediction_count == 1


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        pytest.param(
            date(2026, 3, 29),
            datetime(2026, 3, 28, 23, 0, tzinfo=UTC),
            id="spring-forward-day",
        ),
        pytest.param(
            date(2026, 10, 25),
            datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
            id="fall-back-day",
        ),
        pytest.param(
            date(2026, 1, 15),
            datetime(2026, 1, 14, 23, 0, tzinfo=UTC),
            id="winter-day",
        ),
    ],
)
def test_the_sql_boundary_is_the_clock_boundary(
    database: Database, day: date, expected: datetime
) -> None:
    """L7: PostgreSQL and :func:`kickoff_boundary` name the same instant."""
    stored = _stored_sql_boundary(database.engine, day)

    assert stored == kickoff_boundary(day)
    assert stored == expected
