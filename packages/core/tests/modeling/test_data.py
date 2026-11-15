"""Integration tests for :func:`lmi_core.modeling.data.load_match_data`.

The loader is the only door from PostgreSQL into the modelling arrays, so these
tests observe it through a live database: each case inserts ORM rows, runs the
loader and reads the returned arrays. The fixture needs a disposable database
and fails rather than skips when it is missing, because a missing database is a
configuration error, not a missing capability.
"""

import datetime
import decimal
import os
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import pytest
from lmi_core.config import Settings
from lmi_core.db.alembic_support import upgrade_to_head
from lmi_core.db.models import Match, MatchOdd, Season, Team
from lmi_core.db.seed import seed
from lmi_core.db.session import make_engine, session_factory
from lmi_core.db.testing import require_disposable_database
from lmi_core.modeling.data import load_match_data
from lmi_core.modeling.types import MatchData
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

POSTGRES_ENVIRONMENT_VARIABLES = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)
"""The variables the fixture requires; a missing one is a hard failure."""


@dataclass(frozen=True)
class Database:
    """The throwaway database and the settings that reach it."""

    engine: Engine
    settings: Settings


@pytest.fixture
def database() -> Iterator[Database]:
    """Yield an engine over an empty schema at the head revision, seeded.

    Raises:
        pytest.fail: the destructive-test guard refused the environment, or one
            of the ``POSTGRES_*`` variables is missing. The message names the
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
        upgrade_to_head(settings)
        seed(engine)
        yield Database(engine=engine, settings=settings)
    finally:
        engine.dispose()


def season_id(session: Session, start_year: int) -> int:
    """Return the id of the seeded season that starts in ``start_year``."""
    value = session.scalar(select(Season.id).where(Season.start_year == start_year))
    assert value is not None, f"the seed has no season starting in {start_year}"
    return value


def add_teams(session: Session) -> tuple[int, int]:
    """Insert two teams and return their ids."""
    home = Team(name="Loader Test Home")
    away = Team(name="Loader Test Away")
    session.add_all([home, away])
    session.flush()
    return home.id, away.id


def add_match(
    session: Session,
    *,
    start_year: int,
    home_team_id: int,
    away_team_id: int,
    kickoff_date: datetime.date,
    played: bool = True,
    home_goals: int = 2,
    away_goals: int = 1,
) -> Match:
    """Insert one match and return it, with the result derived from the score."""
    if home_goals > away_goals:
        result = "H"
    elif home_goals == away_goals:
        result = "D"
    else:
        result = "A"
    match = Match(
        season_id=season_id(session, start_year),
        kickoff_date=kickoff_date,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        status="played" if played else "scheduled",
        home_goals=home_goals if played else None,
        away_goals=away_goals if played else None,
        result=result if played else None,
    )
    session.add(match)
    session.flush()
    return match


def add_odds(
    session: Session,
    match: Match,
    bookmaker: str,
    *,
    home: str = "2.100",
    draw: str = "3.300",
    away: str = "3.600",
    is_closing: bool = False,
) -> None:
    """Insert one 1x2 odds row for ``match``."""
    session.add(
        MatchOdd(
            match_id=match.id,
            bookmaker=bookmaker,
            market="1x2",
            is_closing=is_closing,
            home=decimal.Decimal(home),
            draw=decimal.Decimal(draw),
            away=decimal.Decimal(away),
        )
    )
    session.flush()


def load(factory: sessionmaker[Session]) -> MatchData:
    """Run the loader in a session of its own and return its result."""
    with factory() as session:
        return load_match_data(session)


def test_pinnacle_wins_over_the_other_bookmakers(database: Database) -> None:
    factory = session_factory(database.engine)
    with factory() as session:
        home, away = add_teams(session)
        match = add_match(
            session,
            start_year=2010,
            home_team_id=home,
            away_team_id=away,
            kickoff_date=datetime.date(2010, 9, 11),
        )
        add_odds(session, match, "bet365", home="2.200", draw="3.400", away="3.500")
        add_odds(session, match, "market_avg", home="2.150", draw="3.350", away="3.550")
        add_odds(session, match, "market_max", home="2.300", draw="3.500", away="3.700")
        add_odds(session, match, "pinnacle", home="2.100", draw="3.300", away="3.600")
        session.commit()

    data = load(factory)

    assert data.odds_source == ("pinnacle",)
    np.testing.assert_allclose(data.odds[0], [2.1, 3.3, 3.6])


def test_market_avg_is_used_when_it_is_the_only_quote(database: Database) -> None:
    factory = session_factory(database.engine)
    with factory() as session:
        home, away = add_teams(session)
        match = add_match(
            session,
            start_year=2010,
            home_team_id=home,
            away_team_id=away,
            kickoff_date=datetime.date(2010, 9, 18),
        )
        add_odds(session, match, "market_avg", home="2.250", draw="3.400", away="3.450")
        session.commit()

    data = load(factory)

    assert data.odds_source == ("market_avg",)
    np.testing.assert_allclose(data.odds[0], [2.25, 3.4, 3.45])


def test_market_max_alone_leaves_the_odds_row_empty(database: Database) -> None:
    factory = session_factory(database.engine)
    with factory() as session:
        home, away = add_teams(session)
        match = add_match(
            session,
            start_year=2010,
            home_team_id=home,
            away_team_id=away,
            kickoff_date=datetime.date(2010, 9, 25),
        )
        add_odds(session, match, "market_max", home="2.500", draw="3.600", away="4.000")
        session.commit()

    data = load(factory)

    assert data.odds_source == (None,)
    assert bool(np.isnan(data.odds).all())


def test_closing_odds_alone_leave_the_odds_row_empty(database: Database) -> None:
    factory = session_factory(database.engine)
    with factory() as session:
        home, away = add_teams(session)
        match = add_match(
            session,
            start_year=2010,
            home_team_id=home,
            away_team_id=away,
            kickoff_date=datetime.date(2010, 10, 2),
        )
        add_odds(session, match, "pinnacle", is_closing=True)
        add_odds(session, match, "bet365", is_closing=True)
        session.commit()

    data = load(factory)

    assert data.odds_source == (None,)
    assert bool(np.isnan(data.odds).all())


def test_rows_are_ordered_by_kickoff_date_and_id_and_scheduled_matches_are_skipped(
    database: Database,
) -> None:
    factory = session_factory(database.engine)
    with factory() as session:
        home, away = add_teams(session)
        late = add_match(
            session,
            start_year=2013,
            home_team_id=home,
            away_team_id=away,
            kickoff_date=datetime.date(2015, 3, 1),
        )
        add_match(
            session,
            start_year=2014,
            home_team_id=home,
            away_team_id=away,
            kickoff_date=datetime.date(2015, 2, 15),
            played=False,
        )
        first_same_day = add_match(
            session,
            start_year=2011,
            home_team_id=home,
            away_team_id=away,
            kickoff_date=datetime.date(2015, 2, 1),
        )
        second_same_day = add_match(
            session,
            start_year=2012,
            home_team_id=home,
            away_team_id=away,
            kickoff_date=datetime.date(2015, 2, 1),
        )
        session.commit()

    data = load(factory)

    assert list(data.match_id) == [first_same_day.id, second_same_day.id, late.id]
    assert list(data.kickoff_date) == [
        np.datetime64("2015-02-01"),
        np.datetime64("2015-02-01"),
        np.datetime64("2015-03-01"),
    ]


def test_arrays_carry_the_dtypes_the_model_boundary_requires(database: Database) -> None:
    factory = session_factory(database.engine)
    with factory() as session:
        home, away = add_teams(session)
        match = add_match(
            session,
            start_year=2010,
            home_team_id=home,
            away_team_id=away,
            kickoff_date=datetime.date(2010, 10, 16),
        )
        add_odds(session, match, "pinnacle")
        session.commit()

    data = load(factory)

    assert data.match_id.dtype == np.int64
    assert data.kickoff_date.dtype == np.dtype("datetime64[D]")
    assert data.season_id.dtype == np.int64
    assert data.home_team_id.dtype == np.int64
    assert data.away_team_id.dtype == np.int64
    assert data.odds.dtype == np.float64
    assert data.odds.shape == (1, 3)
    assert data.home_goals.dtype == np.int64
    assert data.away_goals.dtype == np.int64
    assert data.result.dtype == np.int8
    assert isinstance(data.odds_source, tuple)
