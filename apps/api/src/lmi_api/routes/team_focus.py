"""Team focus endpoint: one team's season as a single read.

The resource answers one question a dashboard asks on every screen: how is this
club doing in this season. It is a read over rows other services already own.
The played matches are stored, the season table is the one the standings
endpoint serves, and the simulated outlook is a stored row of a finished
simulation run.

No rule of its own is implemented here. The ranking comes from
:func:`lmi_core.standings.compute_standings`, which is the only ranking in the
codebase, and the outcome of every upcoming prediction comes from
:func:`lmi_core.live.scoring.evaluate`, which is the only place that decides
whether a stored forecast was on time. The form and the venue splits count goals
and points over the matches the read layer selects, through the shared readers
of :mod:`lmi_api.views`, so this module only composes a payload.

The endpoint is served on the ``/teams`` prefix and under the ``teams`` tag of
the M2 team resource, so the documentation groups the focus view with the team
it describes and a generated client sees one resource group per entity rather
than one per path shape. The route only nests deeper; ``teams.py`` is not
edited.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Final

from fastapi import APIRouter, HTTPException
from lmi_core.clock import madrid_today
from lmi_core.db.models import Match as MatchModel
from lmi_core.db.models import Team as TeamModel
from lmi_core.live.schemas import EvaluatedPrediction
from lmi_core.live.scoring import evaluate
from lmi_core.standings import StandingRow as CoreStandingRow
from lmi_core.standings import compute_standings
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from lmi_api import views
from lmi_api.db import SessionDep
from lmi_api.errors import PROBLEM_RESPONSES
from lmi_api.routes.teams import team_not_found
from lmi_api.schemas.standings import StandingRow
from lmi_api.schemas.team_focus import TeamFocus, TeamSplits, UpcomingMatch
from lmi_api.schemas.teams import Team

router = APIRouter(prefix="/teams", tags=["teams"])

_FORM_LENGTH: Final[int] = 5
"""How many played matches the form summary covers: the five most recent."""

_UPCOMING_LENGTH: Final[int] = 5
"""How many fixtures still to be played the focus view lists."""


@router.get("/{team_id}/focus", responses=PROBLEM_RESPONSES)
def get_team_focus(team_id: int, session: SessionDep, season: str | None = None) -> TeamFocus:
    """Return the focus view of one team in one season.

    Three things are stored and served as they are: the team, the played
    matches the form and the splits are counted from, and, when a simulation
    succeeded, that run's row for the team. Everything else is derived on read.
    The table row comes from :func:`lmi_core.standings.compute_standings` over
    the season's team set and its played results, ``form`` and ``splits`` count
    goals and points over the same matches, and the ``evaluation`` of every
    upcoming prediction comes from :func:`lmi_core.live.scoring.evaluate`. No
    ranking and no scoring rule is implemented in this module, so the focus view
    cannot disagree with the standings, the predictions or the live metrics
    endpoints.

    ``season`` selects the season; without it the season that starts latest
    among the seasons with a match is served, exactly as the standings endpoint
    selects it. The team has to appear in at least one match of that season,
    whatever the status of that match: the focus view of a club that does not
    play the season would describe a table row that cannot exist.

    ``standing`` is the team's row of the season table. The field is nullable
    because the payload is also the shape a read over a partial table would
    return, but the team-set rule makes it non-null for every team this endpoint
    serves, so a null here means the read is broken rather than the data.

    ``form`` covers the last five played matches of the season, newest first,
    and ``splits`` covers every played match of the season by venue, so the two
    differ in scope and not in arithmetic. ``points_per_game`` is null for a
    side that played nothing, because an average over no game does not exist.

    ``upcoming`` lists the team's next five fixtures still to be played, in
    kickoff order, each with the standing forecast of every model stored for it.
    The forecasts of all five are read in one statement and evaluated once, so
    the number of queries does not grow with the number of fixtures, and a
    fixture without a stored forecast still appears with an empty list.

    ``outlook`` is the team's row of the latest succeeded simulation run of the
    season and is null when the season has no succeeded run, or when that run
    stored no row for the team.

    The spec §6 items this resource deliberately does not carry are M5: expected
    points against actual points, rolling trends, top/middle/bottom splits, the
    multi-season trend, the relegation race with its survival threshold, and how
    much a result moves the relegation probability. Each of them needs a stored
    input or a core rule this task does not own, not another field here.

    The request issues at most fourteen statements whatever the size of the
    payload: the team, the season, the season membership, two for the season
    table, two for the form, two for the splits, three for the upcoming fixtures
    and one for the outlook. A request without played or scheduled matches
    issues fewer, never more.

    The return annotation is the response model, so no ``response_model`` is
    passed.

    Raises:
        HTTPException: 404 when no team has ``team_id``, when ``season`` names
            no stored season, or when the team plays no match of the resolved
            season. The first detail quotes the path parameter, which is the
            resource being addressed; the other two are fixed sentences and
            never repeat the value the client sent.
    """
    stored_team = session.scalar(select(TeamModel).where(TeamModel.id == team_id))
    if stored_team is None:
        raise team_not_found(team_id)
    team = Team.model_validate(stored_team)

    season_id, season_code = views.resolve_season(session, code=season)
    _raise_if_team_does_not_play_the_season(session, team_id=team_id, season_id=season_id)

    teams, results = views.load_season_standings(session, season_id=season_id)
    played = views.load_played_summaries(session, team_id=team_id, season_id=season_id)

    return TeamFocus(
        team=team,
        season=season_code,
        standing=_standing_row_of(team_id, compute_standings(teams, results), teams),
        form=views.form_summary(
            team,
            views.load_played_summaries(
                session, team_id=team_id, season_id=season_id, limit=_FORM_LENGTH
            ),
        ),
        splits=TeamSplits(
            home=views.split_record(team_id, played, side="home"),
            away=views.split_record(team_id, played, side="away"),
        ),
        upcoming=_upcoming(session, season_id=season_id, team_id=team_id),
        outlook=views.load_latest_simulation_outlook(session, season_id=season_id, team_id=team_id),
    )


def _raise_if_team_does_not_play_the_season(
    session: Session, *, team_id: int, season_id: int
) -> None:
    """Raise the 404 of a team that appears in no match of the season.

    The probe is a single capped statement and it runs before the table is
    computed, because the two misses are different: an unknown season and an
    unknown team are answered by their own resources, while a known team that
    plays another season names nothing this season can describe. The detail is a
    fixed sentence and never repeats the identifier the client sent.

    Raises:
        HTTPException: 404 when the team is neither the home nor the away side
            of any match of the season, whatever the status of those matches.
    """
    appears = session.scalar(
        select(MatchModel.id)
        .where(
            MatchModel.season_id == season_id,
            or_(MatchModel.home_team_id == team_id, MatchModel.away_team_id == team_id),
        )
        .limit(1)
    )
    if appears is None:
        raise HTTPException(status_code=404, detail="Team not found in the season")


def _standing_row_of(
    team_id: int, rows: Sequence[CoreStandingRow], teams: Mapping[int, str]
) -> StandingRow | None:
    """Return the payload row of ``team_id`` out of a computed table.

    The mapping is the one the standings endpoint applies: the core row keeps a
    team identifier and a form tuple, while the payload nests the team and turns
    the tuple into a list. The row is looked up rather than recomputed, so the
    position, the counters and the tie-breaks are the ones the ranking decided.

    ``None`` is returned when the table holds no row for the team. The team-set
    rule of :func:`lmi_api.views.load_season_standings` makes that unreachable
    for a team this endpoint serves, which is why the caller keeps the field
    nullable but a null is evidence of a broken read.
    """
    located = next((row for row in rows if row.team_id == team_id), None)
    if located is None:
        return None
    return StandingRow(
        position=located.position,
        team=Team(id=located.team_id, name=teams[located.team_id]),
        played=located.played,
        won=located.won,
        drawn=located.drawn,
        lost=located.lost,
        goals_for=located.goals_for,
        goals_against=located.goals_against,
        goal_difference=located.goal_difference,
        points=located.points,
        form=list(located.form),
    )


def _upcoming(session: Session, *, season_id: int, team_id: int) -> list[UpcomingMatch]:
    """Return the next fixtures of one team, with the forecasts stored for them.

    A fixture qualifies when it is ``scheduled``, dated on or after Madrid today
    and played by ``team_id`` on either side: an earlier date is a fixture whose
    result the source has not published yet, and listing it as upcoming would
    present a match of the past as one still to come, while another club's
    fixture is not this team's next match at all. The order is the kickoff date
    and then the identifier ascending, so the same day keeps a stable order, and
    the list is capped at :data:`_UPCOMING_LENGTH`.

    The forecasts of every listed fixture are read in one statement, evaluated
    once and grouped by match, so the read does not issue one query per fixture.
    The briefs are the standing row of each model, which is what
    :func:`lmi_api.views.prediction_briefs` returns, so a fixture with two models
    lists them in report order.
    """
    match_ids = list(
        session.scalars(
            select(MatchModel.id)
            .where(
                MatchModel.season_id == season_id,
                MatchModel.status == "scheduled",
                MatchModel.kickoff_date >= madrid_today(),
                or_(MatchModel.home_team_id == team_id, MatchModel.away_team_id == team_id),
            )
            .order_by(MatchModel.kickoff_date, MatchModel.id)
            .limit(_UPCOMING_LENGTH)
        )
    )
    stored = views.load_stored_predictions(session, match_ids=match_ids)
    prediction_ids = {entry.identity: entry.id for entry in stored}
    by_match: dict[int, list[EvaluatedPrediction]] = defaultdict(list)
    for row in evaluate([entry.row for entry in stored]):
        by_match[row.match_id].append(row)

    summaries = views.load_match_summaries(session, match_ids)
    return [
        UpcomingMatch(
            match=summaries[match_id],
            predictions=views.prediction_briefs(
                by_match.get(match_id, []), prediction_ids=prediction_ids
            ),
        )
        for match_id in match_ids
    ]
