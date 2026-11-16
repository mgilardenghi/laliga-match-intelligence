"""Support for the live-prediction tests: a throwaway database and raw inserts.

The integration fixture mirrors the one in ``test_live_guards_integration.py``:
it refuses a non-disposable database, drops and recreates ``public``, migrates
to ``head`` and seeds one synthetic season with two synthetic teams. There is no
``conftest.py`` in this package on purpose, so the fixture lives here and the
test modules import it explicitly with ``from live_support import database``.

Every row a test stores is synthetic: team names, dates, odds and scorelines are
placeholders, and no real club, league or bookmaker data appears.
"""

import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import cast

import alembic.command
import pytest
from lmi_core.config import Settings
from lmi_core.db.alembic_support import alembic_config
from lmi_core.db.session import make_engine
from lmi_core.db.testing import require_disposable_database
from lmi_core.live.predict import ModelCounts, PredictedRow, PredictionBatch
from sqlalchemy import text
from sqlalchemy.engine import Engine

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
"""One synthetic team row."""

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
"""One synthetic match, with the result derived by :func:`store_match`."""

_SELECT_MADRID_DATES = """
SELECT today - 1 AS yesterday, today, today + 1 AS tomorrow
FROM (SELECT (now() AT TIME ZONE 'Europe/Madrid')::date AS today) AS dates
"""
"""Yesterday, today and tomorrow on the Madrid calendar, from one statement.

One statement is the point: two calls could straddle Madrid midnight and give a
scenario a today that is not the today of its tomorrow.
"""

_INSERT_ODD = """
INSERT INTO match_odds (match_id, bookmaker, market, is_closing, home, draw, away)
VALUES (:match_id, :bookmaker, :market, :is_closing, :home, :draw, :away)
"""
"""One synthetic price for one match, bookmaker and snapshot."""


@dataclass(frozen=True)
class Database:
    """The throwaway database, its settings, and the seeded rows."""

    engine: Engine
    settings: Settings
    season_id: int
    home_team_id: int
    away_team_id: int


@pytest.fixture
def database() -> Iterator[Database]:
    """Yield a migrated database with one season and two synthetic teams.

    Raises:
        pytest.fail: the destructive-test guard refused the environment, or one
            of the ``POSTGRES_*`` variables is missing.
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
                connection.execute(text(_INSERT_TEAM), {"name": "Synthetic Home"}).scalar_one()
            )
            away_team_id = int(
                connection.execute(text(_INSERT_TEAM), {"name": "Synthetic Away"}).scalar_one()
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


def store_team(engine: Engine, name: str) -> int:
    """Insert one synthetic team and return its id."""
    with engine.begin() as connection:
        return int(connection.execute(text(_INSERT_TEAM), {"name": name}).scalar_one())


def store_match(
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
    """Insert one synthetic match and return its id.

    A played match with goals and no ``result`` gets the result its scoreline
    implies. A match that is not played stores no goals and no result, so the
    database CHECK constraints keep holding.
    """
    if status == "played":
        if home_goals is None or away_goals is None:
            raise ValueError("a played match needs both goal counts")
        if result is None:
            if home_goals > away_goals:
                result = "H"
            elif home_goals == away_goals:
                result = "D"
            else:
                result = "A"
    else:
        if home_goals is not None or away_goals is not None or result is not None:
            raise ValueError("a match that is not played stores neither goals nor a result")
    with engine.begin() as connection:
        return int(
            connection.execute(
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
        )


def madrid_dates(engine: Engine) -> tuple[date, date, date]:
    """Return yesterday, today and tomorrow on the Madrid calendar, from one query.

    A test that needs a fixture after today and an as-of date of today must read
    both from one statement, or a run that crosses Madrid midnight would give
    the scenario a today that is not the today of its fixture.
    """
    with engine.connect() as connection:
        row = connection.execute(text(_SELECT_MADRID_DATES)).one()
    return (
        cast(date, row.yesterday),
        cast(date, row.today),
        cast(date, row.tomorrow),
    )


def make_prediction_batch(
    *,
    as_of: date,
    match_ids: Sequence[int],
    models: Sequence[str] = ("home_win",),
    n_history: int = 3,
    horizon_days: int = 7,
    data_fingerprint: str = "synthetic-fingerprint",
) -> PredictionBatch:
    """Build a batch with one uniform row per match and model.

    The store tests use this instead of converging a real model: the batch is
    only a contract to persist, and the probabilities are a valid triple that
    sums to one so the database CHECK constraints hold.
    """
    rows = tuple(
        PredictedRow(
            match_id=match_id,
            model=model,
            model_version="test-1",
            model_config={"seed": 0},
            p_home=0.5,
            p_draw=0.3,
            p_away=0.2,
            odds_source=None,
        )
        for model in models
        for match_id in match_ids
    )
    counts = tuple(
        ModelCounts(
            model=model,
            n_fixtures=len(match_ids),
            n_rows=len(match_ids),
            n_no_forecast=0,
        )
        for model in models
    )
    return PredictionBatch(
        as_of=as_of,
        horizon_days=horizon_days,
        n_history=n_history,
        n_fixtures=len(match_ids),
        data_fingerprint=data_fingerprint,
        rows=rows,
        counts=counts,
    )


def store_odds(
    engine: Engine,
    *,
    match_id: int,
    bookmaker: str,
    home: float,
    draw: float,
    away: float,
    market: str = "1x2",
    is_closing: bool = False,
) -> None:
    """Insert one synthetic pre-closing price for one match and bookmaker."""
    with engine.begin() as connection:
        connection.execute(
            text(_INSERT_ODD),
            {
                "match_id": match_id,
                "bookmaker": bookmaker,
                "market": market,
                "is_closing": is_closing,
                "home": home,
                "draw": draw,
                "away": away,
            },
        )
