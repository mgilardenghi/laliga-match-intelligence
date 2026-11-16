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

The per-fixture probabilities and the next-match block come from the latest
succeeded simulation of the season. Each fixture's triple is that run's stored
probability of the three results from the club's side, and the block is the
run's stored outcome counts of the club's next fixture, one swing per result
read through :func:`lmi_core.analytics.relegation.next_match_swing`. Both stay
null when the run did not store them: for a run created before revision 0004,
for a fixture the run did not simulate, and, for the block, under the freshness
and never-sampled rules of that core function as well. They are declared
nullable in the contract for exactly those cases, and a probability the run
never measured is never served as zero.

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
    OutcomeCounts,
    OutcomeSwing,
    RankedTeam,
    SeasonTable,
    nearest_rivals,
    next_match_swing,
    survival_threshold,
)
from lmi_core.db.models import SimulationFixture as SimulationFixtureModel
from lmi_core.db.models import SimulationNextMatch as SimulationNextMatchModel
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
    NextMatch,
    NextMatchCurrent,
    NextMatchOutcome,
    NextMatchOutcomes,
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
    the run statement keeps the rivals from costing a lookup per club. The four
    probabilities are read even though only the relegation one is served in
    ``rivals``: the other two are the baseline the next-match block compares its
    swings against, and reading them here keeps both from costing a statement.
    """

    team_id: int
    team_name: str
    current_points: int
    expected_points: float
    expected_position: float
    p_relegation: float
    p_europe: float
    p_title: float
    points_p05: int
    points_p95: int


class _FixtureProbability(NamedTuple):
    """The run's probability of each result, read from the club's side of the fixture.

    ``p_win``, ``p_draw`` and ``p_loss`` always describe the club, whatever venue
    it plays at, so the caller can serve them without knowing which end of the
    stored row the club is.
    """

    p_win: float
    p_draw: float
    p_loss: float


class _Detail(NamedTuple):
    """The stored detail of the selected run, as far as the run holds it.

    ``probabilities`` is keyed by match identifier and holds only the fixtures
    the run simulated, and ``next_match`` is ``None`` whenever the core rule
    answered unknown or the run holds no row for the club.
    """

    probabilities: Mapping[int, _FixtureProbability]
    next_match: NextMatch | None


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

    ``p_win``, ``p_draw`` and ``p_loss`` are the latest succeeded run's
    probability of the fixture's three results from the club's side: at home the
    stored ``p_home``, ``p_draw`` and ``p_away``, away the same triple read from
    the other end, so ``p_win`` means the club wins wherever it plays. They are
    null when that run stored no probability for the fixture, which is the case
    for a run created before revision 0004 and for a fixture the run did not
    simulate.

    ``next_match`` is the first of those fixtures and what each of its three
    results does to the club, read from the same run's stored outcome counts
    through :func:`lmi_core.analytics.relegation.next_match_swing`. Each outcome
    carries the simulated frequency of the result, so ``p_outcome`` may differ
    slightly -- about half a percentage point at 10 000 runs -- from the model
    probability of that same fixture in ``remaining_fixtures``: the swing is a
    frequency of the run's samples, the fixture triple is the model's
    probability. Its ``current`` is the club's own row of that same run, so a
    client computes every delta of the block against one consistent baseline.
    The block is null when the core rule answers unknown -- a run created before
    revision 0004, a stored match that is no longer the club's next fixture, or a
    result the run never sampled -- and it is null as well when the run holds no
    row for the club, because ``current`` is that row and a swing without its
    baseline would misstate every delta.

    The request issues at most eleven statements whatever the number of seasons
    and fixtures: the club, the season resolution, every stored season, every
    match of every season, the names of those clubs, the latest succeeded run
    with its team rows and their club names, the run's fixture probabilities, its
    next-match counts, the history runs, the history team rows and the fixture
    summaries. The club's own fixtures and the resolved season's table are picked
    out of the all-matches statement in Python, so no read here is one statement
    per season or per fixture. Six of the eleven are conditional: the names read
    is skipped when no match is stored at all, the fixture probabilities and the
    next-match counts are issued only when the season has a succeeded run and the
    club a remaining fixture, the two history statements only when the race has
    rivals, and the summary statement only when the club has a remaining fixture.
    A race over a season with a match, without a succeeded run and without a
    remaining fixture stops after six.

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
    run_id, as_of_date, n_runs, run_rows = _load_latest_run(session, season_id=season_id)
    scheduled = _scheduled(season_matches, team_id=team_id)
    detail = _run_detail(
        session,
        run_id=run_id,
        n_runs=n_runs,
        scheduled=scheduled,
        rows=run_rows,
        team_id=team_id,
    )
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
            session,
            scheduled,
            team_id=team_id,
            positions=positions,
            probabilities=detail.probabilities,
        ),
        next_match=detail.next_match,
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
) -> tuple[UUID | None, datetime.date | None, int | None, list[_RunRow]]:
    """Return the latest succeeded run of the season with its sample size and team rows.

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

    ``n_runs`` is the run's own sample size, which the next-match swing divides
    its stored counts by. It is read here instead of in a statement of its own so
    the run is selected once, and it is ``None`` exactly when no run succeeded.

    Returns:
        The run identifier, its as-of date, its sample size and its team rows in
        projected table order. All four are empty when no run succeeded.
    """
    latest = (
        select(SimulationRunModel.id, SimulationRunModel.as_of_date, SimulationRunModel.n_runs)
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
            latest.c.n_runs,
            SimulationTeamResultModel.team_id,
            TeamModel.name,
            SimulationTeamResultModel.current_points,
            SimulationTeamResultModel.mean_points,
            SimulationTeamResultModel.mean_position,
            SimulationTeamResultModel.p_relegation,
            SimulationTeamResultModel.p_europe,
            SimulationTeamResultModel.p_title,
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
    n_runs: int | None = None
    team_rows: list[_RunRow] = []
    for row in session.execute(statement).all():
        run_id = cast(UUID, row.id)
        as_of_date = row.as_of_date
        n_runs = int(row.n_runs)
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
                p_europe=float(row.p_europe),
                p_title=float(row.p_title),
                points_p05=int(row.points_p05),
                points_p95=int(row.points_p95),
            )
        )
    return run_id, as_of_date, n_runs, team_rows


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


def _scheduled(matches: Sequence[views.MatchRow], *, team_id: int) -> list[views.MatchRow]:
    """Return the club's scheduled fixtures of those matches, in the order given.

    Only a ``scheduled`` match is a fixture still to play: a ``withdrawn`` one
    will not be played, and a ``played`` one is a result the table has already
    counted. The caller passes the season's matches in kickoff order, so the
    selection keeps it, and the list is what both the detail reads and the served
    fixtures are built from.
    """
    return [
        row
        for row in matches
        if row.status == "scheduled" and team_id in (row.home_team_id, row.away_team_id)
    ]


def _run_detail(
    session: Session,
    *,
    run_id: UUID | None,
    n_runs: int | None,
    scheduled: Sequence[views.MatchRow],
    rows: Sequence[_RunRow],
    team_id: int,
) -> _Detail:
    """Return the run's fixture probabilities and next-match block, or empty ones.

    Both detail reads need a succeeded run and a club with a remaining fixture:
    without the run there is nothing stored to read, and without a fixture there
    is no fixture to price and no next match to describe. Either way the answer
    is an empty mapping and a null ``next_match``, so the caller serves three
    nulls per fixture rather than a probability nobody stored.

    The first statement reads the run's probability triple of every scheduled
    fixture of the club, mapped onto the club's side. The second reads the run's
    outcome counts of the club's next match, and
    :func:`lmi_core.analytics.relegation.next_match_swing` turns them into the
    three swings. Both statements are issued before the club's row is looked
    for, because the two conditions above are the run and the fixture: whether
    the run holds a row for the club decides what is served, never which
    statements the read issues. A ``None`` from that function is served as a null
    ``next_match``, and so is a run that holds no row for the club: ``current`` is
    that row, and a swing without its baseline would misstate every delta.

    ``n_runs`` is the run's sample size, which the swing divides its counts by;
    it is null only when no run succeeded, which the first condition covers.
    """
    if run_id is None or n_runs is None or not scheduled:
        return _Detail(probabilities={}, next_match=None)
    probabilities = _fixture_probabilities(
        session, run_id=run_id, scheduled=scheduled, team_id=team_id
    )
    counts = _outcome_counts(session, run_id=run_id, team_id=team_id)
    subject = next((row for row in rows if row.team_id == team_id), None)
    if subject is None:
        return _Detail(probabilities=probabilities, next_match=None)
    swings = next_match_swing(counts, n_runs=n_runs, match_id=scheduled[0].id)
    if swings is None:
        return _Detail(probabilities=probabilities, next_match=None)
    by_outcome = {swing.outcome: swing for swing in swings}
    return _Detail(
        probabilities=probabilities,
        next_match=NextMatch(
            match_id=scheduled[0].id,
            outcomes=NextMatchOutcomes(
                win=_next_match_outcome(by_outcome["win"]),
                draw=_next_match_outcome(by_outcome["draw"]),
                loss=_next_match_outcome(by_outcome["loss"]),
            ),
            current=NextMatchCurrent(
                p_relegation=subject.p_relegation,
                p_europe=subject.p_europe,
                p_title=subject.p_title,
                expected_points=subject.expected_points,
            ),
        ),
    )


def _fixture_probabilities(
    session: Session, *, run_id: UUID, scheduled: Sequence[views.MatchRow], team_id: int
) -> Mapping[int, _FixtureProbability]:
    """Return the run's triple of each scheduled fixture, keyed by match identifier.

    One statement selects the run's stored fixtures among the club's scheduled
    matches, so a run that simulated only some of them costs no extra read. Each
    stored triple is then read from the club's side: at home ``p_home`` is its
    win probability and ``p_away`` its loss, away the two swap, because the stored
    row describes the two clubs and the payload describes one of them.
    """
    statement = select(
        SimulationFixtureModel.match_id,
        SimulationFixtureModel.p_home,
        SimulationFixtureModel.p_draw,
        SimulationFixtureModel.p_away,
    ).where(
        SimulationFixtureModel.run_id == run_id,
        SimulationFixtureModel.match_id.in_([row.id for row in scheduled]),
    )
    at_home = {row.id: row.home_team_id == team_id for row in scheduled}
    probabilities: dict[int, _FixtureProbability] = {}
    for row in session.execute(statement).all():
        p_home = float(row.p_home)
        p_draw = float(row.p_draw)
        p_away = float(row.p_away)
        probabilities[int(row.match_id)] = (
            _FixtureProbability(p_win=p_home, p_draw=p_draw, p_loss=p_away)
            if at_home[int(row.match_id)]
            else _FixtureProbability(p_win=p_away, p_draw=p_draw, p_loss=p_home)
        )
    return probabilities


def _outcome_counts(session: Session, *, run_id: UUID, team_id: int) -> list[OutcomeCounts]:
    """Return the run's stored outcome counts of the club's next match, one per result.

    One statement reads every row the run holds for the club. The rows name the
    match they describe, which is what lets
    :func:`lmi_core.analytics.relegation.next_match_swing` answer ``None`` when
    the club's next fixture has moved on since the run was stored: the counts are
    then about a match that is no longer next.
    """
    statement = select(
        SimulationNextMatchModel.match_id,
        SimulationNextMatchModel.outcome,
        SimulationNextMatchModel.n_outcome,
        SimulationNextMatchModel.n_relegated,
        SimulationNextMatchModel.n_europe,
        SimulationNextMatchModel.n_title,
        SimulationNextMatchModel.points_sum,
    ).where(
        SimulationNextMatchModel.run_id == run_id,
        SimulationNextMatchModel.team_id == team_id,
    )
    return [
        OutcomeCounts(
            match_id=int(row.match_id),
            outcome=str(row.outcome),
            n_outcome=int(row.n_outcome),
            n_relegated=int(row.n_relegated),
            n_europe=int(row.n_europe),
            n_title=int(row.n_title),
            points_sum=int(row.points_sum),
        )
        for row in session.execute(statement).all()
    ]


def _next_match_outcome(swing: OutcomeSwing) -> NextMatchOutcome:
    """Return one core swing as the payload's outcome, every number stored as it is."""
    return NextMatchOutcome(
        p_outcome=swing.p_outcome,
        p_relegation=swing.p_relegation,
        p_europe=swing.p_europe,
        p_title=swing.p_title,
        expected_points=swing.expected_points,
    )


def _remaining_fixtures(
    session: Session,
    scheduled: Sequence[views.MatchRow],
    *,
    team_id: int,
    positions: Mapping[int, int],
    probabilities: Mapping[int, _FixtureProbability],
) -> list[RemainingFixture]:
    """Return the club's scheduled fixtures, in kickoff order, with what is known of each.

    ``scheduled`` is already the club's still-to-play fixtures, so this function
    only pairs each of them with the stored fixture summary, the venue the club
    plays it at, the opponent's place in the resolved season's table and the
    run's probability triple from the club's side. A club the table does not rank
    keeps ``None`` rather than a borrowed place, and a fixture the run did not
    simulate keeps three nulls rather than a probability nobody stored.

    The summaries are read through :func:`lmi_api.views.load_match_summaries` in
    one statement for the whole list.
    """
    summaries = views.load_match_summaries(session, [row.id for row in scheduled])
    fixtures: list[RemainingFixture] = []
    for row in scheduled:
        at_home = row.home_team_id == team_id
        opponent_id = row.away_team_id if at_home else row.home_team_id
        venue: Venue = "home" if at_home else "away"
        probability = probabilities.get(row.id)
        fixtures.append(
            RemainingFixture(
                match=summaries[row.id],
                venue=venue,
                opponent_position=positions.get(opponent_id),
                p_win=None if probability is None else probability.p_win,
                p_draw=None if probability is None else probability.p_draw,
                p_loss=None if probability is None else probability.p_loss,
            )
        )
    return fixtures
