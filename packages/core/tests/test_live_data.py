"""Tests for the live loaders: the odds-priority drift guard and the date split.

The unit test can run without a database. The integration test needs the
throwaway PostgreSQL of :mod:`live_support` and is deselected by ``make test``.
Every match, team and price here is synthetic.
"""

import datetime

import numpy as np
import pytest
from live_support import Database, database, store_match, store_odds, store_team  # noqa: F401
from lmi_core.live import data as live_data
from lmi_core.modeling import data as modeling_data
from sqlalchemy.orm import Session


def test_odds_priority_matches_the_modeling_loader() -> None:
    assert live_data.ODDS_PRIORITY == modeling_data._BOOKMAKERS_BY_PRIORITY


@pytest.mark.integration
def test_loaders_split_played_and_upcoming(database: Database) -> None:  # noqa: F811
    as_of = datetime.date(2026, 5, 13)
    engine = database.engine
    third = store_team(engine, "Synthetic Third")
    fourth = store_team(engine, "Synthetic Fourth")
    home = database.home_team_id
    away = database.away_team_id

    played_id = store_match(
        engine,
        season_id=database.season_id,
        home_team_id=home,
        away_team_id=away,
        kickoff_date=as_of - datetime.timedelta(days=3),
        status="played",
        home_goals=2,
        away_goals=1,
    )
    at_as_of_id = store_match(
        engine,
        season_id=database.season_id,
        home_team_id=away,
        away_team_id=home,
        kickoff_date=as_of,
    )
    priced_id = store_match(
        engine,
        season_id=database.season_id,
        home_team_id=third,
        away_team_id=fourth,
        kickoff_date=as_of + datetime.timedelta(days=2),
    )
    later_id = store_match(
        engine,
        season_id=database.season_id,
        home_team_id=fourth,
        away_team_id=third,
        kickoff_date=as_of + datetime.timedelta(days=20),
    )
    unpriced_id = store_match(
        engine,
        season_id=database.season_id,
        home_team_id=third,
        away_team_id=home,
        kickoff_date=as_of + datetime.timedelta(days=4),
    )

    store_odds(engine, match_id=priced_id, bookmaker="bet365", home=1.9, draw=3.6, away=4.2)
    store_odds(engine, match_id=priced_id, bookmaker="market_avg", home=2.1, draw=3.4, away=3.9)
    store_odds(engine, match_id=priced_id, bookmaker="pinnacle", home=2.0, draw=3.5, away=4.0)
    store_odds(
        engine,
        match_id=priced_id,
        bookmaker="bet365",
        home=1.7,
        draw=3.8,
        away=4.8,
        is_closing=True,
    )

    with Session(engine) as session:
        history = live_data.load_history(session, as_of)
        upcoming = live_data.load_upcoming(session, as_of, as_of + datetime.timedelta(days=7))

    assert history.match_id.tolist() == [played_id]
    assert history.kickoff_date.tolist() == [as_of - datetime.timedelta(days=3)]

    assert upcoming.match_id.tolist() == [priced_id, unpriced_id]
    assert upcoming.kickoff_date.tolist() == [
        as_of + datetime.timedelta(days=2),
        as_of + datetime.timedelta(days=4),
    ]
    assert later_id not in upcoming.match_id.tolist()
    assert at_as_of_id not in upcoming.match_id.tolist()

    assert np.isnan(upcoming.odds[1]).all()
    assert upcoming.odds_source == ("pinnacle", None)
    np.testing.assert_array_equal(upcoming.odds[0], np.array([2.0, 3.5, 4.0]))


@pytest.mark.integration
def test_load_upcoming_never_attaches_a_closing_price(database: Database) -> None:  # noqa: F811
    """A fixture with only a closing price must not look priced at all.

    The other test always carries a ``pinnacle`` pre-closing price, so a leaked
    closing price would still lose on priority there. Here the closing price is
    the only one, and the loader must return an all-NaN odds row instead.
    """
    as_of = datetime.date(2026, 5, 13)
    engine = database.engine
    closing_only_id = store_match(
        engine,
        season_id=database.season_id,
        home_team_id=database.home_team_id,
        away_team_id=database.away_team_id,
        kickoff_date=as_of + datetime.timedelta(days=2),
    )
    store_odds(
        engine,
        match_id=closing_only_id,
        bookmaker="pinnacle",
        home=1.5,
        draw=4.0,
        away=6.0,
        is_closing=True,
    )

    with Session(engine) as session:
        upcoming = live_data.load_upcoming(session, as_of, as_of + datetime.timedelta(days=7))

    assert upcoming.match_id.tolist() == [closing_only_id]
    assert np.isnan(upcoming.odds[0]).all()
    assert upcoming.odds_source == (None,)
