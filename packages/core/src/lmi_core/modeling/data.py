"""Load played matches from PostgreSQL into :class:`~lmi_core.modeling.types.MatchData`.

This module is the one place under ``lmi_core.modeling`` that may import
SQLAlchemy and the database models. Every other module of the package works on
the columnar arrays only, so the evaluation math runs without a database and
cannot reach a table a model is not allowed to see. The loader is therefore the
single boundary where stored rows become the arrays a model may read.

The odds row selected per match is an early 1x2 price, never the closing one:
the closing price is only known at kickoff, so a model fitted on it would look
better than it could be in production. ``market_max`` is an aggregate that no
single bookmaker quoted, so it is excluded as well. Between the remaining
bookmakers ``pinnacle`` wins over ``market_avg`` and ``bet365`` because it is
the sharpest price and the one the market model is built around.
"""

import datetime
from collections.abc import Mapping
from typing import Final

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from lmi_core.db.models import Match, MatchOdd
from lmi_core.modeling.types import MatchData

RESULT_INDEX: Final[Mapping[str, int]] = {"H": 0, "D": 1, "A": 2}
"""The stored ``CHAR(1)`` result mapped to the :data:`~lmi_core.modeling.types.OUTCOMES` index."""

_BOOKMAKERS_BY_PRIORITY: Final[tuple[str, ...]] = ("pinnacle", "market_avg", "bet365")
"""The bookmakers the loader accepts, most trusted first; ``market_max`` is never used."""


def load_match_data(session: Session) -> MatchData:
    """Return every played match with its goals, result and one early 1x2 price.

    Rows are ordered by ``(kickoff_date, id)``, which is the order the columnar
    types require. A match without an eligible odds row keeps an all-NaN odds
    row and a ``None`` source, so no model has to special-case missing prices.

    Args:
        session: a session bound to the application database.

    Returns:
        The played matches, sorted by kickoff date and match id. An empty
        database produces an empty :class:`~lmi_core.modeling.types.MatchData`.

    Raises:
        ValueError: a played match has no goals or no result, or its stored
            result is not one of ``H``, ``D`` or ``A``. The message names the
            match id.
    """
    priority = case(
        (MatchOdd.bookmaker == "pinnacle", 0),
        (MatchOdd.bookmaker == "market_avg", 1),
        (MatchOdd.bookmaker == "bet365", 2),
        else_=3,
    )
    odds = (
        select(
            MatchOdd.match_id.label("match_id"),
            MatchOdd.bookmaker.label("bookmaker"),
            MatchOdd.home.label("odds_home"),
            MatchOdd.draw.label("odds_draw"),
            MatchOdd.away.label("odds_away"),
        )
        .where(
            MatchOdd.is_closing.is_(False),
            MatchOdd.market == "1x2",
            MatchOdd.bookmaker.in_(_BOOKMAKERS_BY_PRIORITY),
        )
        .ext(distinct_on(MatchOdd.match_id))
        .order_by(MatchOdd.match_id, priority)
        .subquery()
    )
    statement = (
        select(
            Match.id.label("match_id"),
            Match.kickoff_date,
            Match.season_id,
            Match.home_team_id,
            Match.away_team_id,
            Match.home_goals,
            Match.away_goals,
            Match.result,
            odds.c.bookmaker,
            odds.c.odds_home,
            odds.c.odds_draw,
            odds.c.odds_away,
        )
        .outerjoin(odds, odds.c.match_id == Match.id)
        .where(Match.status == "played")
        .order_by(Match.kickoff_date, Match.id)
    )

    match_ids: list[int] = []
    kickoff_dates: list[datetime.date] = []
    season_ids: list[int] = []
    home_team_ids: list[int] = []
    away_team_ids: list[int] = []
    home_goals: list[int] = []
    away_goals: list[int] = []
    results: list[int] = []
    odds_values: list[tuple[float, float, float]] = []
    odds_sources: list[str | None] = []

    for row in session.execute(statement):
        match_id = int(row.match_id)
        home = row.home_goals
        away = row.away_goals
        stored_result = row.result
        if home is None or away is None or stored_result is None:
            raise ValueError(
                f"played match {match_id} has no goals or no result; a played match must carry both"
            )
        result = RESULT_INDEX.get(str(stored_result).strip())
        if result is None:
            known = ", ".join(RESULT_INDEX)
            raise ValueError(
                f"played match {match_id} has result {stored_result!r}; expected one of {known}"
            )

        match_ids.append(match_id)
        kickoff_dates.append(row.kickoff_date)
        season_ids.append(int(row.season_id))
        home_team_ids.append(int(row.home_team_id))
        away_team_ids.append(int(row.away_team_id))
        home_goals.append(int(home))
        away_goals.append(int(away))
        results.append(result)
        if row.bookmaker is None:
            odds_sources.append(None)
            odds_values.append((np.nan, np.nan, np.nan))
        else:
            odds_sources.append(str(row.bookmaker))
            odds_values.append((float(row.odds_home), float(row.odds_draw), float(row.odds_away)))

    n = len(match_ids)
    kickoff: NDArray[np.datetime64] = np.array(kickoff_dates, dtype="datetime64[D]")
    return MatchData(
        match_id=np.array(match_ids, dtype=np.int64),
        kickoff_date=kickoff,
        season_id=np.array(season_ids, dtype=np.int64),
        home_team_id=np.array(home_team_ids, dtype=np.int64),
        away_team_id=np.array(away_team_ids, dtype=np.int64),
        odds=np.array(odds_values, dtype=np.float64).reshape(n, 3),
        odds_source=tuple(odds_sources),
        home_goals=np.array(home_goals, dtype=np.int64),
        away_goals=np.array(away_goals, dtype=np.int64),
        result=np.array(results, dtype=np.int8),
    )
