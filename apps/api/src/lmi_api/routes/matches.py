"""Match endpoints."""

import datetime
import decimal
from typing import Annotated, Literal, cast

from fastapi import APIRouter, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from lmi_core.db.models import Match as MatchModel
from lmi_core.db.models import MatchStat as MatchStatModel
from lmi_core.db.models import Season as SeasonModel
from lmi_core.db.models import Team as TeamModel
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session, aliased

from lmi_api.db import SessionDep
from lmi_api.errors import PROBLEM_RESPONSES
from lmi_api.schemas.matches import (
    MatchDetail,
    MatchOutcome,
    MatchPage,
    MatchStats,
    MatchStatus,
    MatchSummary,
)
from lmi_api.schemas.teams import Team

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", responses=PROBLEM_RESPONSES)
def list_matches(
    session: SessionDep,
    season: str | None = None,
    team_id: int | None = None,
    matchday: Annotated[int | None, Query(ge=1, le=46)] = None,
    status: MatchStatus | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    order: Literal["asc", "desc"] = "asc",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MatchPage:
    """Return one page of fixtures, most chronological first by default.

    Every filter is optional and the ones given are combined with ``AND``: a
    season code, a team that played home or away, a matchday, a status, and an
    inclusive range of kickoff dates. The two dates are compared on
    ``kickoff_date`` only, so a range covers whole days and a match without a
    kickoff time is still in it.

    ``total`` is the number of fixtures the filter matched in the whole
    database, not the size of ``items``, so a client renders a result count and
    asks for the next page without a second call.

    The sort key is the kickoff date, then the kickoff time, then the
    identifier. A fixture whose source published no time therefore never jumps
    to the top of its day and never leads the list in reverse: the null time
    sorts last in both directions, while the date and the identifier still
    reverse. Within one day ``desc`` reverses the relative order of the timed
    fixtures and still ends with the untimed ones, so the two orders agree on a
    day that holds one timed fixture and diverge once a day holds two.

    The request path issues two statements, one for the count and one for the
    page, whatever the size of the page: both teams come from explicit joins on
    the same statement, because the models declare no relationship the code
    could lazy-load per row. The only exception is a page that matched nothing
    while a season code or a team identifier was given: one existence probe per
    value given, so at most two, decides between an empty page and a 404. The
    probes run after the count and before the page statement, because a miss is
    only possible when the count is zero: an unknown value raises there, a known
    one falls through to the page query.

    Raises:
        HTTPException: 404 when ``season`` or ``team_id`` names no stored row.
            The detail is a fixed sentence and never repeats the value, so the
            response says which kind of thing is unknown and nothing more.
        RequestValidationError: 422 when ``date_from`` is after ``date_to``,
            rendered by the installed handler as a problem details body that
            names the offending parameter and not the dates.
    """
    if date_from is not None and date_to is not None and date_from > date_to:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("query", "date_to"),
                    "msg": "date_from must not be after date_to",
                }
            ]
        )

    conditions: list[ColumnElement[bool]] = []
    if season is not None:
        conditions.append(SeasonModel.code == season)
    if team_id is not None:
        conditions.append(
            or_(MatchModel.home_team_id == team_id, MatchModel.away_team_id == team_id)
        )
    if matchday is not None:
        conditions.append(MatchModel.matchday == matchday)
    if status is not None:
        conditions.append(MatchModel.status == status)
    if date_from is not None:
        conditions.append(MatchModel.kickoff_date >= date_from)
    if date_to is not None:
        conditions.append(MatchModel.kickoff_date <= date_to)

    total = session.scalar(
        select(func.count())
        .select_from(MatchModel)
        .join(SeasonModel, MatchModel.season_id == SeasonModel.id)
        .where(*conditions)
    )
    if total is None:
        total = 0
    if total == 0:
        _raise_if_filter_value_is_unknown(session, season=season, team_id=team_id)

    home_team = aliased(TeamModel)
    away_team = aliased(TeamModel)
    if order == "desc":
        sort_key = (
            MatchModel.kickoff_date.desc(),
            MatchModel.kickoff_time.desc().nulls_last(),
            MatchModel.id.desc(),
        )
    else:
        sort_key = (
            MatchModel.kickoff_date.asc(),
            MatchModel.kickoff_time.asc().nulls_last(),
            MatchModel.id.asc(),
        )
    page_statement = (
        select(MatchModel, SeasonModel.code, home_team, away_team)
        .join(SeasonModel, MatchModel.season_id == SeasonModel.id)
        .join(home_team, MatchModel.home_team_id == home_team.id)
        .join(away_team, MatchModel.away_team_id == away_team.id)
        .where(*conditions)
        .order_by(*sort_key)
        .limit(limit)
        .offset(offset)
    )
    rows = session.execute(page_statement).all()
    return MatchPage(
        items=[_summary(match, season_code, home, away) for match, season_code, home, away in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{match_id}", responses=PROBLEM_RESPONSES)
def get_match(match_id: int, session: SessionDep) -> MatchDetail:
    """Return one fixture with its statistics.

    The statistics row is fetched by an outer join on the same statement, so a
    match without statistics is one statement with a null row and not a second
    request.

    Raises:
        HTTPException: 404 when no stored match has ``match_id``. The detail is
            a fixed sentence and never repeats the identifier.
    """
    home_team = aliased(TeamModel)
    away_team = aliased(TeamModel)
    statement = (
        select(MatchModel, SeasonModel.code, home_team, away_team, MatchStatModel)
        .join(SeasonModel, MatchModel.season_id == SeasonModel.id)
        .join(home_team, MatchModel.home_team_id == home_team.id)
        .join(away_team, MatchModel.away_team_id == away_team.id)
        .outerjoin(MatchStatModel, MatchStatModel.match_id == MatchModel.id)
        .where(MatchModel.id == match_id)
    )
    row = session.execute(statement).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Match not found")
    match, season_code, home, away, stats = row
    summary = _summary(match, season_code, home, away)
    return MatchDetail(**summary.model_dump(), stats=_stats(stats))


def _raise_if_filter_value_is_unknown(
    session: Session, *, season: str | None, team_id: int | None
) -> None:
    """Raise the 404 of a filter value that names no stored row.

    It is called only when a filtered page is empty, because the count alone
    cannot tell an unknown season code or team identifier from a known one that
    matches no fixture. The detail is a fixed sentence: the value the client
    sent is not repeated, and the season and team endpoints follow the same rule.

    Raises:
        HTTPException: ``season`` is set and no season has that code, or
            ``team_id`` is set and no team has that identifier.
    """
    if season is not None:
        season_id = session.scalar(select(SeasonModel.id).where(SeasonModel.code == season))
        if season_id is None:
            raise HTTPException(status_code=404, detail="Season not found")
    if team_id is not None:
        stored_id = session.scalar(select(TeamModel.id).where(TeamModel.id == team_id))
        if stored_id is None:
            raise HTTPException(status_code=404, detail="Team not found")


def _summary(match: MatchModel, season_code: str, home: TeamModel, away: TeamModel) -> MatchSummary:
    """Return the payload of one fixture row and its two joined teams.

    The row is built from the columns the statement selected, never from a
    relationship: the models declare none, and reading an attribute the
    statement did not load would issue one query per row.

    ``status`` and ``result`` are narrowed with a cast because the ORM types
    them as plain text: a check constraint restricts them to the values of their
    literal, so the cast states a rule the database already enforces.
    """
    return MatchSummary(
        id=match.id,
        season=season_code,
        kickoff_date=match.kickoff_date,
        kickoff_time=match.kickoff_time,
        matchday=match.matchday,
        status=cast(MatchStatus, match.status),
        home_team=Team(id=home.id, name=home.name),
        away_team=Team(id=away.id, name=away.name),
        home_goals=match.home_goals,
        away_goals=match.away_goals,
        ht_home_goals=match.ht_home_goals,
        ht_away_goals=match.ht_away_goals,
        result=cast(MatchOutcome | None, match.result),
    )


def _stats(row: MatchStatModel | None) -> MatchStats | None:
    """Return the payload of one statistics row, or ``None`` when there is none.

    The expected goals are converted from the decimal the column stores to the
    float the payload declares, explicitly, because the database round-trips
    them as :class:`decimal.Decimal` and a payload field typed as a float must
    not carry one.
    """
    if row is None:
        return None
    return MatchStats(
        home_shots=row.home_shots,
        away_shots=row.away_shots,
        home_shots_on_target=row.home_shots_on_target,
        away_shots_on_target=row.away_shots_on_target,
        home_fouls=row.home_fouls,
        away_fouls=row.away_fouls,
        home_corners=row.home_corners,
        away_corners=row.away_corners,
        home_yellow=row.home_yellow,
        away_yellow=row.away_yellow,
        home_red=row.home_red,
        away_red=row.away_red,
        home_xg=_as_float(row.home_xg),
        away_xg=_as_float(row.away_xg),
    )


def _as_float(value: decimal.Decimal | None) -> float | None:
    """Return a stored decimal as a float, keeping a null as ``None``."""
    return None if value is None else float(value)
