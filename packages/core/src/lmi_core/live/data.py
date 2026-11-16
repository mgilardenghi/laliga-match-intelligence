"""The one place under :mod:`lmi_core.live` that reads PostgreSQL.

Mirroring :mod:`lmi_core.modeling.data`, every live query lives here so the rest
of the package works on arrays and DTOs only. The loader is the boundary where
stored rows become what a model or a report may see, and it applies the same
odds rule as the modeling loader: pre-closing 1x2 prices, one per match, most
trusted bookmaker first, and an all-NaN row plus a ``None`` source when a match
has no eligible price.

The live direction is the opposite of the modeling one: history is strictly
before the as-of date, and fixtures start strictly after it. A match dated on
the as-of date is neither, because no source provides a verified kickoff
instant, so it cannot be proven to be in the future.
"""

import datetime
from typing import Final, Literal, cast

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from lmi_core.db.models import Match, MatchOdd, Prediction
from lmi_core.live.schemas import PredictionRow
from lmi_core.modeling.data import load_match_data
from lmi_core.modeling.types import Fixtures, History

ODDS_PRIORITY: Final[tuple[str, ...]] = ("pinnacle", "market_avg", "bet365")
"""The bookmakers a live fixture accepts, most trusted first.

This priority must match :data:`lmi_core.modeling.data._BOOKMAKERS_BY_PRIORITY`:
the live fixtures and the played history have to price a match the same way, or
the market model would be fitted and scored against different odds.
"""

_MATCH_STATUSES: Final = ("scheduled", "played", "withdrawn")
_RESULTS: Final = ("H", "D", "A")

_MatchStatus = Literal["scheduled", "played", "withdrawn"]
_Result = Literal["H", "D", "A"]


def load_history(session: Session, before: datetime.date) -> History:
    """Return the played matches dated strictly before ``before``.

    The played set and its odds come from
    :func:`lmi_core.modeling.data.load_match_data`; this function only slices
    the result, so the two loaders cannot disagree on which matches or prices a
    fit may use.

    Args:
        session: a session bound to the application database.
        before: the exclusive upper date bound, normally the as-of date.

    Returns:
        The played matches with ``kickoff_date < before``, sorted by
        ``(kickoff_date, match_id)``. An empty result is a valid empty history.
    """
    played = load_match_data(session)
    mask = played.kickoff_date < np.datetime64(before, "D")
    return played.select(mask)


def load_upcoming(session: Session, after: datetime.date, until: datetime.date) -> Fixtures:
    """Return the scheduled matches dated strictly after ``after`` and up to ``until``.

    The dates are compared on the Madrid calendar, the only calendar the
    database stores. ``after`` is exclusive and ``until`` inclusive, so a run on
    day ``C`` with a seven-day horizon covers ``C+1`` through ``C+7`` and never
    the matches of ``C`` itself.

    Args:
        session: a session bound to the application database.
        after: the exclusive lower date bound, normally the as-of date.
        until: the inclusive upper date bound.

    Returns:
        The scheduled fixtures, sorted by ``(kickoff_date, match_id)``, with one
        early 1x2 price per match where an eligible bookmaker quoted one. A
        fixture without a price keeps an all-NaN odds row and a ``None`` source.
        An empty result is a valid empty :class:`~lmi_core.modeling.types.Fixtures`.
    """
    priority = case(
        *((MatchOdd.bookmaker == name, rank) for rank, name in enumerate(ODDS_PRIORITY)),
        else_=len(ODDS_PRIORITY),
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
            MatchOdd.bookmaker.in_(ODDS_PRIORITY),
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
            odds.c.bookmaker,
            odds.c.odds_home,
            odds.c.odds_draw,
            odds.c.odds_away,
        )
        .outerjoin(odds, odds.c.match_id == Match.id)
        .where(
            Match.status == "scheduled",
            Match.kickoff_date > after,
            Match.kickoff_date <= until,
        )
        .order_by(Match.kickoff_date, Match.id)
    )

    match_ids: list[int] = []
    kickoff_dates: list[datetime.date] = []
    season_ids: list[int] = []
    home_team_ids: list[int] = []
    away_team_ids: list[int] = []
    odds_values: list[tuple[float, float, float]] = []
    odds_sources: list[str | None] = []

    for row in session.execute(statement):
        match_ids.append(int(row.match_id))
        kickoff_dates.append(row.kickoff_date)
        season_ids.append(int(row.season_id))
        home_team_ids.append(int(row.home_team_id))
        away_team_ids.append(int(row.away_team_id))
        if row.bookmaker is None:
            odds_sources.append(None)
            odds_values.append((np.nan, np.nan, np.nan))
        else:
            odds_sources.append(str(row.bookmaker))
            odds_values.append((float(row.odds_home), float(row.odds_draw), float(row.odds_away)))

    n = len(match_ids)
    kickoff: NDArray[np.datetime64] = np.array(kickoff_dates, dtype="datetime64[D]")
    return Fixtures(
        match_id=np.array(match_ids, dtype=np.int64),
        kickoff_date=kickoff,
        season_id=np.array(season_ids, dtype=np.int64),
        home_team_id=np.array(home_team_ids, dtype=np.int64),
        away_team_id=np.array(away_team_ids, dtype=np.int64),
        odds=np.array(odds_values, dtype=np.float64).reshape(n, 3),
        odds_source=tuple(odds_sources),
    )


def load_prediction_rows(session: Session, *, model: str | None = None) -> list[PredictionRow]:
    """Return the stored predictions joined with the match they belong to.

    The natural key of a stored prediction is ``(match_id, model, as_of_date)``,
    so the rows are ordered by that key and then by ``created_at`` to give every
    consumer one deterministic order.

    Args:
        session: a session bound to the application database.
        model: restrict the result to one registered model name; ``None`` reads
            every stored prediction.

    Returns:
        Frozen :class:`~lmi_core.live.schemas.PredictionRow` values, or an empty
        list when nothing has been stored yet.

    Raises:
        ValueError: a played match carries no result or a result outside
            ``H``, ``D`` and ``A``. The message names the match id.
    """
    statement = (
        select(
            Prediction.match_id,
            Prediction.model,
            Prediction.model_version,
            Prediction.as_of_date,
            Prediction.created_at,
            Prediction.p_home,
            Prediction.p_draw,
            Prediction.p_away,
            Match.kickoff_date,
            Match.status,
            Match.result,
        )
        .join(Match, Match.id == Prediction.match_id)
        .order_by(
            Prediction.match_id,
            Prediction.model,
            Prediction.as_of_date,
            Prediction.created_at,
        )
    )
    if model is not None:
        statement = statement.where(Prediction.model == model)

    rows: list[PredictionRow] = []
    for row in session.execute(statement):
        match_id = int(row.match_id)
        status = str(row.status)
        if status not in _MATCH_STATUSES:
            raise ValueError(
                f"match {match_id} has status {status!r}; expected one of "
                + ", ".join(_MATCH_STATUSES)
            )
        result = _stored_result(match_id, status, row.result)
        rows.append(
            PredictionRow(
                match_id=match_id,
                model=str(row.model),
                model_version=str(row.model_version),
                as_of_date=row.as_of_date,
                created_at=row.created_at,
                p_home=float(row.p_home),
                p_draw=float(row.p_draw),
                p_away=float(row.p_away),
                kickoff_date=row.kickoff_date,
                match_status=cast(_MatchStatus, status),
                result=result,
            )
        )
    return rows


def _stored_result(match_id: int, status: str, stored: object) -> _Result | None:
    """Return the stored result text, or ``None`` when the match was not played."""
    if status != "played":
        return None
    if stored is None:
        raise ValueError(f"played match {match_id} has no result; a played match must carry one")
    text = str(stored).strip()
    if text not in _RESULTS:
        raise ValueError(
            f"played match {match_id} has result {stored!r}; expected one of " + ", ".join(_RESULTS)
        )
    return cast(_Result, text)
