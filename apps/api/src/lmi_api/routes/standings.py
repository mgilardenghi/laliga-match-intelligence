"""Standing endpoints."""

import datetime
from typing import cast

from fastapi import APIRouter, HTTPException
from lmi_core.db.models import Match as MatchModel
from lmi_core.db.models import Season as SeasonModel
from lmi_core.db.models import Team as TeamModel
from lmi_core.standings import MatchResult, compute_standings
from sqlalchemy import select
from sqlalchemy.orm import Session

from lmi_api.db import SessionDep
from lmi_api.errors import PROBLEM_RESPONSES
from lmi_api.schemas.standings import StandingRow, Standings
from lmi_api.schemas.teams import Team

router = APIRouter(prefix="/standings", tags=["standings"])


@router.get("", responses=PROBLEM_RESPONSES)
def get_standings(
    session: SessionDep,
    season: str | None = None,
    as_of: datetime.date | None = None,
) -> Standings:
    """Return the table of one season, optionally cut off at a date.

    Without ``season`` the answer is the season that starts latest among the
    seasons with at least one match, which is the season a dashboard opens on
    once ingestion has loaded it. ``as_of`` restricts the counted results to
    the matches whose kickoff date is on or before it, the boundary included, so
    a client can reconstruct the table as it stood after a given matchday from
    the same endpoint that serves the current one.

    The team set is every team that appears as the home or the away side in any
    match of the season, whatever the status of that match, so a club whose only
    fixture is still scheduled keeps a row of zeros and the table has the same
    shape before and after the first matchday. The counted results are the
    ``played`` matches only: a scheduled fixture contributes nothing even when
    its kickoff date has passed, which is the state of a postponed match the
    source has not played yet. A season without a single match returns an empty
    table with status 200, not an error.

    The ranking is not computed here: the rows, their order, their points, their
    tie-breaks and the form string all come from
    :func:`lmi_core.standings.compute_standings`, called once with the team set
    and the counted results. This module only decides which season, which teams
    and which results the ranking sees, and maps the core rows to the payload.
    ``matches_counted`` is the size of the result list handed to it.

    The request issues at most three statements: one to resolve the season, one
    for the team set and one for the results, whatever the number of rows. The
    team set is read with an explicit join on a union of the two team columns
    because the models declare no relationship the code could lazy-load per row.

    Raises:
        HTTPException: 404 when ``season`` is given and no season has that code,
            or when no season has a match while ``season`` is absent. The detail
            is a fixed sentence in both cases and never repeats the client
            value, unlike the season and team endpoints, which quote their own
            path parameter because that is the resource being addressed.
    """
    season_id, season_code = _resolve_season(session, season=season)

    team_ids = (
        select(MatchModel.home_team_id.label("team_id"))
        .where(MatchModel.season_id == season_id)
        .union(
            select(MatchModel.away_team_id.label("team_id")).where(
                MatchModel.season_id == season_id
            )
        )
        .subquery()
    )
    team_statement = select(TeamModel.id, TeamModel.name).join(
        team_ids, TeamModel.id == team_ids.c.team_id
    )
    teams: dict[int, str] = {
        team_id: name for team_id, name in session.execute(team_statement).all()
    }

    result_statement = select(
        MatchModel.home_team_id,
        MatchModel.away_team_id,
        MatchModel.home_goals,
        MatchModel.away_goals,
        MatchModel.kickoff_date,
    ).where(
        MatchModel.season_id == season_id,
        MatchModel.status == "played",
        # The ``played_complete`` check constraint makes both goal columns
        # non-null for a played row, so the filter states the rule the database
        # already enforces and the casts below narrow what it guarantees.
        MatchModel.home_goals.is_not(None),
        MatchModel.away_goals.is_not(None),
    )
    if as_of is not None:
        result_statement = result_statement.where(MatchModel.kickoff_date <= as_of)
    results = [
        MatchResult(
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_goals=cast(int, home_goals),
            away_goals=cast(int, away_goals),
            kickoff_date=kickoff_date,
        )
        for home_team_id, away_team_id, home_goals, away_goals, kickoff_date in session.execute(
            result_statement
        ).all()
    ]

    rows = [
        StandingRow(
            position=row.position,
            team=Team(id=row.team_id, name=teams[row.team_id]),
            played=row.played,
            won=row.won,
            drawn=row.drawn,
            lost=row.lost,
            goals_for=row.goals_for,
            goals_against=row.goals_against,
            goal_difference=row.goal_difference,
            points=row.points,
            form=list(row.form),
        )
        for row in compute_standings(teams, results)
    ]
    return Standings(
        season=season_code,
        as_of=as_of,
        matches_counted=len(results),
        rows=rows,
    )


def _resolve_season(session: Session, *, season: str | None) -> tuple[int, str]:
    """Return the identifier and the code of the season the request selects.

    A given code selects that season. Without one, the selection is the greatest
    ``start_year`` among the seasons with at least one match: the season that
    starts latest is the one a dashboard opens on, and requiring a match keeps
    the answer on a season ingestion has loaded instead of a season that is
    only registered.

    Raises:
        HTTPException: 404 when the code is unknown, or when no season has a
            match while no code was given. The detail is a fixed sentence and
            never repeats the value the client sent.
    """
    if season is not None:
        stored = session.scalars(select(SeasonModel).where(SeasonModel.code == season)).first()
        if stored is None:
            raise HTTPException(status_code=404, detail="Season not found")
        return stored.id, stored.code

    latest = session.scalars(
        select(SeasonModel)
        .where(select(MatchModel.id).where(MatchModel.season_id == SeasonModel.id).exists())
        .order_by(SeasonModel.start_year.desc())
        .limit(1)
    ).first()
    if latest is None:
        raise HTTPException(status_code=404, detail="No season with matches")
    return latest.id, latest.code
