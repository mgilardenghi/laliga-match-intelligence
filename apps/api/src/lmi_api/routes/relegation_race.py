"""Relegation-race endpoint: one club's survival race in one season.

The resource answers how close one club is to the bottom, against what the
league historically demanded. Three of its four blocks are read from rows the
repository already stores: the club's remaining fixtures, the league's own
history and the club's row in the latest succeeded simulation. No derived rule
is implemented here. The survival line and the rival group come from
:mod:`lmi_core.analytics.relegation`, the table from
:func:`lmi_core.standings.compute_standings`, the relegation count from
:func:`lmi_core.simulation.engine.Qualification`, the season series from
:func:`lmi_api.views.load_simulation_history_points`, and the fixture summaries
from :func:`lmi_api.views.load_match_summaries`. The seasons, their matches, the
clubs' names and the season's table come from :func:`lmi_api.views.load_seasons`,
:func:`lmi_api.views.load_season_matches`, :func:`lmi_api.views.load_team_names`
and :func:`lmi_api.views.season_table`, so this route cannot rank a season
differently from the team history or the standings endpoint. This module only
decides which rows the read selects and maps them to the payload.

The R5 per-fixture and next-match projections are not part of this milestone.
``p_win``, ``p_draw``, ``p_loss`` and ``next_match`` are nullable in the
contract so a stored simulation can fill them later without a contract change,
and today the read serves them as ``null`` rather than inventing a number.

The endpoint is served on the ``/teams`` prefix and under the ``teams`` tag of
the team resource, so the documentation groups it with the club it describes.
The route only nests deeper; ``teams.py`` is not edited.
"""

import datetime
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import NamedTuple, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException
from lmi_core.analytics.relegation import (
    RankedTeam,
    SeasonTable,
    nearest_rivals,
    survival_threshold,
)
from lmi_core.db.models import SimulationRun as SimulationRunModel
from lmi_core.db.models import SimulationTeamResult as SimulationTeamResultModel
from lmi_core.db.models import Team as TeamModel
from lmi_core.simulation import Qualification
from sqlalchemy import select
from sqlalchemy.orm import Session

from lmi_api import views
from lmi_api.db import SessionDep
from lmi_api.errors import PROBLEM_RESPONSES
from lmi_api.routes.teams import team_not_found
from lmi_api.schemas.relegation_race import (
    RelegationRace,
    RelegationRival,
    RemainingFixture,
    SurvivalThreshold,
)
from lmi_api.schemas.simulations import SimulationHistory
from lmi_api.schemas.team_season_stats import Venue
from lmi_api.schemas.teams import Team

router = APIRouter(prefix="/teams", tags=["teams"])

_RIVAL_COUNT = 4
"""How many clubs beside the subject the race compares, and whose series it serves.

Four is the width the race view renders. The number lives here, next to the read
that applies it, because the core selector takes it as an argument and must not
decide a product width.
"""


class _RunRow(NamedTuple):
    """One team row of the selected simulation, with the club name beside it.

    The name is joined in because the payload nests the club, and reading it from
    the run statement keeps the rivals from costing a lookup per club.
    """

    team_id: int
    team_name: str
    current_points: int
    expected_points: float
    expected_position: float
    p_relegation: float
    points_p05: int
    points_p95: int


@router.get("/{team_id}/relegation-race", responses=PROBLEM_RESPONSES)
def get_team_relegation_race(
    team_id: int, session: SessionDep, season: str | None = None
) -> RelegationRace:
    """Return the relegation race of one club in one season.

    ``team`` is the stored club and ``season`` the resolved season code, so a
    client can pass the code back to the other endpoints. An omitted season is
    the season that starts latest among the seasons with a match, an unknown code
    is the 404 ``Season not found``, an identifier no stored club has is the 404
    ``Team not found``, and a stored club that is not a side of any match of the
    resolved season is the 404 ``Team not found in season``. Every detail is a
    fixed sentence and none repeats the value the client sent.

    ``survival_threshold`` reads **every** stored season, not the resolved one:
    the line answers what the league usually demanded to survive, and a sample of
    the one season in front of the client would be the current table wearing the
    name of a history. Each season is ranked by
    :func:`lmi_core.standings.compute_standings` over its whole team set and its
    played results, and one :class:`lmi_core.analytics.relegation.SeasonTable`
    per season is handed to
    :func:`lmi_core.analytics.relegation.survival_threshold` with
    ``relegation_positions=Qualification().relegation_positions``. The
    configured count is the only relegation count in the repository -- the
    simulator and the API build every run from it -- so a copy of it here would
    drift from the qualification the stored runs were requested with. The
    completeness rule, the counted place and the median are that module's rules
    and are not restated here; the result is served field for field.

    A ``None`` from the core function -- no stored season is a completed 20-club
    season of 2005-06 or later -- is the 404 ``no completed season``. The field
    is not nullable by design. Falling back to the current table, or serving a
    zero, would read to a client as a survival line somebody computed out of a
    sample that does not exist.

    ``rivals`` is the latest succeeded simulation of the resolved season, one
    row per club, ordered the way
    :func:`lmi_api.views.load_latest_simulation_outlook` orders the run it
    describes: ``finished_at`` descending with the rows still missing that stamp
    last, then ``as_of_date`` descending and finally the identifier descending.
    Only ``succeeded`` counts. A queued, running or failed run measures nothing,
    so serving its partial row would report a simulation that never finished.

    The run's team rows become :class:`lmi_core.analytics.relegation.RankedTeam`
    values and :func:`lmi_core.analytics.relegation.nearest_rivals` cuts them to
    the subject club plus the :data:`_RIVAL_COUNT` clubs nearest to it by
    expected position. The group is served in expected-position order with each
    club's stored current points, mean points, relegation probability and the two
    points quantiles, so the whole group reads in table order. When the selected
    run holds no row for the subject club, ``rivals`` is served empty rather than
    raising: the run exists and the club is simply absent from it, and ``run_id``
    still names the run the rest of the answer was read from.

    With no succeeded run the answer is still a 200: ``run_id`` and
    ``as_of_date`` are null, ``rivals`` is empty and ``series`` holds the season
    code and no point, while the threshold and the remaining fixtures are served
    as usual. A race without a simulation is a state of the data, not an error.

    ``series`` is :func:`lmi_api.views.load_simulation_history_points` of the
    resolved season restricted to the rival identifiers, wrapped in
    :class:`~lmi_api.schemas.simulations.SimulationHistory`. Restricting the read
    is what keeps the series comparable: it is the group's history, not the
    league's, and one point per as-of date of the season comes from the latest
    succeeded run of that date. An empty group serves the season code and no
    point, because there is no club whose history it would be.

    ``remaining_fixtures`` is the club's ``scheduled`` matches of the resolved
    season, ascending by kickoff date and then identifier, each with the stored
    fixture summary, the venue the club plays it at, and the opponent's position
    in the resolved season's table. That table is
    :func:`lmi_core.standings.compute_standings` over the season's team set and
    played results, read out of the same all-matches statement the threshold
    uses, so the two cannot rank the season differently. ``None`` is kept for an
    opponent the table does not rank.

    ``p_win``, ``p_draw`` and ``p_loss`` are always ``null`` today, and so is
    ``next_match``. Those are the R5 per-fixture and next-match simulation
    outputs, which this milestone does not store: the fields are declared
    nullable so a stored simulation can fill them without a contract change, and
    the read serves them as null instead of inventing a number or a placeholder
    model.

    The request issues at most nine statements whatever the number of seasons and
    fixtures: the club, the season resolution, every stored season, every match
    of every season, the names of those clubs, the latest succeeded run with its
    team rows and their club names, the history runs, the history team rows and
    the fixture summaries. The club's own fixtures and the resolved season's
    table are picked out of the all-matches statement in Python, so no read here
    is one statement per season or per fixture. Three of the nine are conditional:
    the names read is skipped when no match is stored at all, the two history
    statements are issued only when the race has rivals, and the summary
    statement only when the club has a remaining fixture. A race over a season
    with a match, without a succeeded run and without a remaining fixture stops
    after six.

    The return annotation is the response model, so no ``response_model`` is
    passed.

    Raises:
        HTTPException: 404 when no stored team has ``team_id``, when ``season``
            names no stored season, when no season has a match while ``season``
            is absent, when the team is not a side of any match of the resolved
            season, or when no stored season is a completed one.
    """
    stored_team = session.scalar(select(TeamModel).where(TeamModel.id == team_id))
    if stored_team is None:
        raise team_not_found(team_id)
    team = Team.model_validate(stored_team)

    season_id, season_code = views.resolve_season(session, code=season)
    seasons = views.load_seasons(session)
    matches = views.load_season_matches(session, season_ids=[row.id for row in seasons])
    season_matches = [row for row in matches if row.season_id == season_id]
    if team_id not in _team_ids(season_matches):
        raise HTTPException(status_code=404, detail="Team not found in season")

    names = views.load_team_names(
        session,
        team_ids=sorted({side for row in matches for side in (row.home_team_id, row.away_team_id)}),
    )
    rows_by_season: dict[int, list[views.MatchRow]] = defaultdict(list)
    for row in matches:
        rows_by_season[row.season_id].append(row)

    threshold = survival_threshold(
        (_season_table(season, rows=rows_by_season[season.id], names=names) for season in seasons),
        relegation_positions=Qualification().relegation_positions,
    )
    if threshold is None:
        raise HTTPException(status_code=404, detail="no completed season")

    positions = {
        row.team_id: row.position for row in views.season_table(season_matches, names=names)
    }
    run_id, as_of_date, run_rows = _load_latest_run(session, season_id=season_id)
    rivals, rival_ids = _rivals(run_rows, team_id=team_id)
    points = (
        views.load_simulation_history_points(session, season_id=season_id, team_ids=rival_ids)
        if rival_ids
        else []
    )
    return RelegationRace(
        team=team,
        season=season_code,
        run_id=run_id,
        as_of_date=as_of_date,
        survival_threshold=SurvivalThreshold(
            points_median=threshold.points_median,
            points_min=threshold.points_min,
            points_max=threshold.points_max,
            seasons_used=threshold.seasons_used,
            position=threshold.position,
        ),
        rivals=[_rival(row) for row in rivals],
        series=SimulationHistory(season=season_code, points=points),
        remaining_fixtures=_remaining_fixtures(
            session, season_matches, team_id=team_id, positions=positions
        ),
        next_match=None,
    )


def _season_table(
    season: views.SeasonRow, *, rows: Sequence[views.MatchRow], names: Mapping[int, str]
) -> SeasonTable:
    """Return one season as the threshold sees it: its size, its results and its totals.

    ``rows`` is every match of that season, so the team set is every club that
    appears as a side, whatever the status of the match, which is the input the
    standings endpoint and the team views also use. ``results_counted`` is the
    number of played matches, which is the proof of completeness the core module
    reads instead of a calendar, and ``points`` holds the table's total for each
    place in table order.
    """
    standings = views.season_table(rows, names=names)
    return SeasonTable(
        season=season.code,
        start_year=season.start_year,
        n_teams=len(standings),
        results_counted=sum(1 for row in rows if row.played),
        points=tuple(standing.points for standing in standings),
    )


def _team_ids(rows: Sequence[views.MatchRow]) -> set[int]:
    """Return every club that appears as a side of those matches."""
    return {side for row in rows for side in (row.home_team_id, row.away_team_id)}


def _load_latest_run(
    session: Session, *, season_id: int
) -> tuple[UUID | None, datetime.date | None, list[_RunRow]]:
    """Return the latest succeeded run of the season with its team rows.

    The run is chosen by the ordering of
    :func:`lmi_api.views.load_latest_simulation_outlook` -- ``finished_at``
    descending with the rows still missing that stamp last, then ``as_of_date``
    descending, then the identifier descending -- so the race and the team focus
    describe the same run. Only ``succeeded`` counts, because a queued, running
    or failed run has written no projection.

    One statement selects the run and its team rows together: the ordered run is
    a subquery with a limit of one, and its rows are outer-joined so a run
    without a team row still comes back, which is what lets the payload name a
    run the subject club is absent from. An empty result means the season has no
    succeeded run.

    Returns:
        The run identifier, its as-of date and its team rows in projected table
        order. All three are empty when no run succeeded.
    """
    latest = (
        select(SimulationRunModel.id, SimulationRunModel.as_of_date)
        .where(
            SimulationRunModel.season_id == season_id,
            SimulationRunModel.status == "succeeded",
        )
        .order_by(
            SimulationRunModel.finished_at.desc().nulls_last(),
            SimulationRunModel.as_of_date.desc(),
            SimulationRunModel.id.desc(),
        )
        .limit(1)
        .subquery()
    )
    statement = (
        select(
            latest.c.id,
            latest.c.as_of_date,
            SimulationTeamResultModel.team_id,
            TeamModel.name,
            SimulationTeamResultModel.current_points,
            SimulationTeamResultModel.mean_points,
            SimulationTeamResultModel.mean_position,
            SimulationTeamResultModel.p_relegation,
            SimulationTeamResultModel.points_p05,
            SimulationTeamResultModel.points_p95,
        )
        .select_from(latest)
        .outerjoin(SimulationTeamResultModel, SimulationTeamResultModel.run_id == latest.c.id)
        .outerjoin(TeamModel, TeamModel.id == SimulationTeamResultModel.team_id)
        .order_by(SimulationTeamResultModel.mean_position, SimulationTeamResultModel.team_id)
    )
    run_id: UUID | None = None
    as_of_date: datetime.date | None = None
    team_rows: list[_RunRow] = []
    for row in session.execute(statement).all():
        run_id = cast(UUID, row.id)
        as_of_date = row.as_of_date
        if row.team_id is None:
            continue
        team_rows.append(
            _RunRow(
                team_id=int(row.team_id),
                team_name=str(row.name),
                current_points=int(row.current_points),
                expected_points=float(row.mean_points),
                expected_position=float(row.mean_position),
                p_relegation=float(row.p_relegation),
                points_p05=int(row.points_p05),
                points_p95=int(row.points_p95),
            )
        )
    return run_id, as_of_date, team_rows


def _rivals(rows: Sequence[_RunRow], *, team_id: int) -> tuple[list[_RunRow], list[int]]:
    """Return the served rival rows and their identifiers, in expected-position order.

    The selection is :func:`lmi_core.analytics.relegation.nearest_rivals`, which
    owns the distance, the tie-break and the order; this function only maps the
    run's rows into its :class:`~lmi_core.analytics.relegation.RankedTeam` input
    and back. A run without a row for the subject club gives back nothing: the
    core selector refuses a club that is not among the rows, and the caller
    serves an empty group instead of substituting another run.
    """
    if all(row.team_id != team_id for row in rows):
        return [], []
    by_id = {row.team_id: row for row in rows}
    group = nearest_rivals(
        [
            RankedTeam(
                team_id=row.team_id,
                current_points=row.current_points,
                expected_points=row.expected_points,
                expected_position=row.expected_position,
                p_relegation=row.p_relegation,
                points_p05=row.points_p05,
                points_p95=row.points_p95,
            )
            for row in rows
        ],
        team_id=team_id,
        count=_RIVAL_COUNT,
    )
    return [by_id[rival_id] for rival_id in group], list(group)


def _rival(row: _RunRow) -> RelegationRival:
    """Return one run row as the payload's rival, every number stored as it is."""
    return RelegationRival(
        team=Team(id=row.team_id, name=row.team_name),
        current_points=row.current_points,
        expected_points=row.expected_points,
        p_relegation=row.p_relegation,
        points_p05=row.points_p05,
        points_p95=row.points_p95,
    )


def _remaining_fixtures(
    session: Session,
    matches: Sequence[views.MatchRow],
    *,
    team_id: int,
    positions: Mapping[int, int],
) -> list[RemainingFixture]:
    """Return the club's scheduled fixtures of the season, in kickoff order.

    Only a ``scheduled`` match is a fixture still to play: a ``withdrawn`` one
    will not be played, and a ``played`` one is a result the table has already
    counted. The opponent's position comes from the resolved season's table, and
    a club the table does not rank keeps ``None`` rather than a borrowed place.

    The three probabilities are null because this milestone stores no per-fixture
    simulation output, and the read serves the stored fixture summary through
    :func:`lmi_api.views.load_match_summaries` in one statement for the whole
    list.
    """
    scheduled = [
        row
        for row in matches
        if row.status == "scheduled" and team_id in (row.home_team_id, row.away_team_id)
    ]
    summaries = views.load_match_summaries(session, [row.id for row in scheduled])
    fixtures: list[RemainingFixture] = []
    for row in scheduled:
        at_home = row.home_team_id == team_id
        opponent_id = row.away_team_id if at_home else row.home_team_id
        venue: Venue = "home" if at_home else "away"
        fixtures.append(
            RemainingFixture(
                match=summaries[row.id],
                venue=venue,
                opponent_position=positions.get(opponent_id),
                p_win=None,
                p_draw=None,
                p_loss=None,
            )
        )
    return fixtures
