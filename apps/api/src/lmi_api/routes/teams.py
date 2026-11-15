"""Team endpoints."""

from fastapi import APIRouter, HTTPException
from lmi_core.db.models import Match as MatchModel
from lmi_core.db.models import Season as SeasonModel
from lmi_core.db.models import Team as TeamModel
from sqlalchemy import or_, select

from lmi_api.db import SessionDep
from lmi_api.errors import PROBLEM_RESPONSES
from lmi_api.routes.seasons import season_not_found
from lmi_api.schemas.teams import Team

router = APIRouter(prefix="/teams", tags=["teams"])


def team_not_found(team_id: int) -> HTTPException:
    """Return the 404 error for a team ``team_id`` that is not stored.

    It returns the error instead of raising it so the caller raises it on the
    line that detected the miss, where the condition is visible. The message
    quotes the identifier the client sent, which is the client's own input.
    """
    return HTTPException(status_code=404, detail=f"Team {team_id} not found")


@router.get("", responses=PROBLEM_RESPONSES)
def list_teams(session: SessionDep, season: str | None = None) -> list[Team]:
    """Return every team, or the teams of one season.

    Without ``season`` every stored team is returned. With ``season`` the answer
    is restricted to the teams that appear as the home or the away side in at
    least one match of that season, whatever the match status: a season whose
    fixtures are not loaded yet is an empty list with status 200, not an error.
    Only an unknown season code is a 404.

    The ordering is by name, with the identifier as tie-break, so two teams with
    the same name still come back in a stable order.

    Raises:
        HTTPException: 404 when ``season`` is given and no season has that code.
    """
    statement = select(TeamModel)
    if season is not None:
        season_id = session.scalar(select(SeasonModel.id).where(SeasonModel.code == season))
        if season_id is None:
            raise season_not_found(season)
        statement = statement.where(
            or_(
                TeamModel.id.in_(
                    select(MatchModel.home_team_id).where(MatchModel.season_id == season_id)
                ),
                TeamModel.id.in_(
                    select(MatchModel.away_team_id).where(MatchModel.season_id == season_id)
                ),
            )
        )
    rows = session.scalars(statement.order_by(TeamModel.name, TeamModel.id))
    return [Team.model_validate(row) for row in rows]


@router.get("/{team_id}", responses=PROBLEM_RESPONSES)
def get_team(team_id: int, session: SessionDep) -> Team:
    """Return one team by its identifier.

    Raises:
        HTTPException: 404 when no stored team has ``team_id``.
    """
    row = session.scalar(select(TeamModel).where(TeamModel.id == team_id))
    if row is None:
        raise team_not_found(team_id)
    return Team.model_validate(row)
