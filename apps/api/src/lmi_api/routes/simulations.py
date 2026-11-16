"""Simulation endpoints: the stored scenario runs of the season simulator.

A request stores a row and hands its identifier to the worker through the job
queue; every read endpoint then answers from PostgreSQL, so the state of a run
and its projected table are readable without asking the broker. This module is
therefore the only write endpoint of the API: it inserts a run, and it writes
the failure state when the queue refuses it.

Three rules shape the reads:

- A season is resolved once per request. An explicit code is probed against the
  ``seasons`` table, so an unknown code is a 404 whatever the runs carry. An
  omitted code selects the season that starts latest among the seasons with
  something to show: a match for the write endpoints, a run for the read ones.
- A projected table is in projected table order, the one the worker prints:
  mean final position ascending, mean points descending, club name, club id.
  The table exists only for a succeeded run, so a client never reads a partial
  table as a complete one.
- The history keeps one point per as-of date, the latest succeeded run of that
  date, ascending. A date without a succeeded run contributes no point.

The models declare no ``relationship()``, so every team and season is read with
an explicit join or an explicit second statement, never by a lazy load.
"""

import logging
import uuid
from typing import Annotated, Any, Final, cast

from fastapi import APIRouter, HTTPException, Query, Response
from lmi_core.clock import madrid_today
from lmi_core.db.models import Match as MatchModel
from lmi_core.db.models import Season as SeasonModel
from lmi_core.db.models import SimulationRun as SimulationRunModel
from lmi_core.db.models import SimulationTeamResult as SimulationTeamResultModel
from lmi_core.db.models import Team as TeamModel
from lmi_core.simulation import Qualification
from lmi_core.simulation.service import create_run
from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from lmi_api.db import SessionDep
from lmi_api.errors import PROBLEM_MEDIA_TYPE, PROBLEM_RESPONSES
from lmi_api.queue import QueueDep
from lmi_api.schemas.common import Problem
from lmi_api.schemas.simulations import (
    SimulationCurrent,
    SimulationDetail,
    SimulationHistory,
    SimulationHistoryPoint,
    SimulationHistoryTeam,
    SimulationKind,
    SimulationModel,
    SimulationPointsDistribution,
    SimulationPointsQuantiles,
    SimulationQualification,
    SimulationRequest,
    SimulationRun,
    SimulationRunPage,
    SimulationStatus,
    SimulationTeamRow,
    SimulationTrigger,
)
from lmi_api.schemas.teams import Team

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/simulations", tags=["simulations"])

LOCATION_PATH: Final = "/v1/simulations/{run_id}"
"""The ``Location`` header template of a created run."""

ENQUEUE_FAILED_DETAIL = "Simulation enqueue failed"
"""The one sentence a 503 answers with.

The cause stays in the server log: the broker's error text can carry the
connection URL, and a response never repeats an internal detail.
"""

ENQUEUE_FAILED_ERROR = "the run could not be handed to the worker"
"""The short text stored in the ``error`` column of a run the queue refused.

It carries no driver text for the same reason: the column is readable through
the API, so it must not become a second way to disclose the broker address.
"""

CREATE_RESPONSES: dict[int | str, dict[str, Any]] = {
    **PROBLEM_RESPONSES,
    503: {"model": Problem, "content": {PROBLEM_MEDIA_TYPE: {}}},
}
"""The error responses a create request declares.

503 is added to the shared set because this is the only endpoint that talks to
the job queue: the run is stored, but the worker cannot be told about it.
"""


@router.post("", status_code=202, responses=CREATE_RESPONSES)
def create_simulation(
    body: SimulationRequest,
    session: SessionDep,
    queue: QueueDep,
    response: Response,
) -> SimulationRun:
    """Ask for a simulation run, store it and hand it to the worker.

    The answer is 202 and not 201: the row is stored, but nothing has been
    simulated yet, and ``run.status`` is ``queued`` until a worker claims it. The
    identifier in the ``Location`` header is where the client polls, and the body
    describes the row exactly as it was stored.

    An omitted ``season`` selects the season that starts latest among the seasons
    with at least one match, which is the season the standings endpoint opens on.
    An omitted ``as_of_date`` is the Madrid date. An ``as_of_date`` later than
    that date is refused before any row is written: the run would count matches
    whose results nobody knows as if they had been played.

    A request identical to an active run does not create a second one. The
    natural key of an active run is the season, the as-of date, the run count,
    the seed and the model, so the same body twice answers the identifier the
    first request produced, with no second message to the worker. The runs that
    already exist and are alive therefore stay the single truth about what has
    been asked for.

    The row is committed before the message is sent, so the worker that receives
    the identifier always finds the row it names. When the queue refuses the
    message and the run is still ``queued``, the run cannot execute, so it is
    marked ``failed`` with the enqueue instant as its start and finish, and the
    request answers 503: a run left ``queued`` would look alive to a client that
    polls it and to every later identical request, which would then be told to
    wait for work nobody is doing. A queued row that is merely stale is instead
    re-enqueued by the next identical request, which is why the failure is
    recorded rather than retried here.

    A message that did reach the broker can be claimed before the enqueue call
    reports its error. Such a claim is not overwritten: the worker owns the run
    and is executing it, so the answer is the ordinary 202 that describes the
    stored row instead of a failure, and the client polls the real state.

    Args:
        body: the season, as-of date, size and seed the client asked for.
        session: the session that inserts the run and records a failure.
        queue: the producer that hands the committed identifier to the worker.
        response: the response whose ``Location`` header names the new run.

    Returns:
        The stored run, as it looks after the operation.

    Raises:
        HTTPException: 422 when ``as_of_date`` is later than the Madrid date, 404
            when the season names no stored season or no season has a match
            while the body names none, and 503 when the queue refused the
            message and the run was recorded as failed.
    """
    today = madrid_today()
    as_of_date = body.as_of_date if body.as_of_date is not None else today
    if as_of_date > today:
        raise HTTPException(
            status_code=422,
            detail="as_of_date must not be later than the Madrid date",
        )

    season = (
        _stored_season(session, body.season)
        if body.season is not None
        else _latest_season_with_matches(session)
    )

    try:
        run, enqueue_required = create_run(
            session,
            season_code=season.code,
            as_of_date=as_of_date,
            n_runs=body.n_runs,
            seed=body.seed,
            trigger="api",
            qualification=Qualification(),
            kind="live",
        )
    except ValueError:
        # ``create_run`` resolves the season again, and the seasons table can
        # change between the probe above and that read.
        raise HTTPException(status_code=404, detail="Season not found") from None

    if enqueue_required:
        try:
            queue.enqueue(run.id)
        except Exception:
            logger.exception("the queue refused simulation run %s", run.id)
            if _mark_enqueue_failed(session, run.id):
                raise HTTPException(status_code=503, detail=ENQUEUE_FAILED_DETAIL) from None

    # The row was committed before the message was sent, so a reload is what
    # describes what is stored, whatever the session still holds in memory.
    session.refresh(run)
    response.headers["Location"] = LOCATION_PATH.format(run_id=run.id)
    return _run_out(run, season.code)


@router.get("", responses=PROBLEM_RESPONSES)
def list_simulations(
    session: SessionDep,
    season: str | None = None,
    status: SimulationStatus | None = None,
    kind: SimulationKind | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SimulationRunPage:
    """Return one page of stored runs, most recently requested first.

    The filters are combined with ``AND`` and all are optional. ``status`` and
    ``kind`` are literals, so a value outside the four states or the two kinds is
    refused by the request validation and never reaches this function. ``season``
    is probed against the ``seasons`` table first, so an unknown code is a 404
    instead of a page that merely came back empty.

    An omitted ``season`` selects the season that starts latest among the seasons
    with at least one run, so a dashboard opens on the season somebody has
    actually simulated. When no season has a run the answer is an empty page with
    status 200, not a 404: the collection exists, it is simply empty.

    ``total`` is the number of runs the filters matched in the whole table, not
    the size of ``items``, so a client renders a result count and asks for the
    next page without a second call. The order is the request instant descending
    with the identifier descending as a tie-break, so two runs requested in the
    same instant still have one order and paging the same filter twice cannot
    move a row between pages.

    The request issues at most three statements: the season probe, one count and
    one page, and the page statement joins the season explicitly because the
    models declare no relationship the payload could lazy-load per row.

    Raises:
        HTTPException: 404 when ``season`` names no stored season. The detail is
            a fixed sentence and never repeats the value the client sent.
    """
    resolved = _resolve_read_season(session, season)
    if resolved is None:
        return SimulationRunPage(items=[], total=0, limit=limit, offset=offset)

    conditions: list[ColumnElement[bool]] = [SimulationRunModel.season_id == resolved.id]
    if status is not None:
        conditions.append(SimulationRunModel.status == status)
    if kind is not None:
        conditions.append(SimulationRunModel.kind == kind)

    count_statement = select(func.count()).select_from(SimulationRunModel).where(*conditions)
    page_statement = (
        select(SimulationRunModel, SeasonModel.code)
        .join(SeasonModel, SeasonModel.id == SimulationRunModel.season_id)
        .where(*conditions)
        .order_by(
            SimulationRunModel.requested_at.desc(),
            SimulationRunModel.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    total = session.scalar(count_statement)
    rows = session.execute(page_statement).all()
    return SimulationRunPage(
        items=[_run_out(run, code) for run, code in rows],
        total=total if total is not None else 0,
        limit=limit,
        offset=offset,
    )


@router.get("/latest", responses=PROBLEM_RESPONSES)
def get_latest_simulation(session: SessionDep, season: str | None = None) -> SimulationDetail:
    """Return the most recent succeeded run of a season with its projected table.

    "Most recent" is the finish instant descending, with the request instant and
    the identifier as tie-breaks, so the answer is the last forecast the season
    produced rather than the last one somebody asked for: a run still queued, or
    one that failed, is not an answer a dashboard can draw.

    An omitted ``season`` selects the season that starts latest among the seasons
    with at least one run, the same rule the collection uses, so the two views
    agree on which season a client is looking at. A season whose runs all failed
    is a 404 and not an empty table: "no succeeded simulation" is a state of the
    season, not a table with no rows.

    The path is declared before ``/{run_id}`` so that ``latest`` is never read as
    a run identifier. The body is the shape of the single-run request, built by
    the same helpers, so the two endpoints cannot drift apart.

    Raises:
        HTTPException: 404 when ``season`` names no stored season, or when the
            selected season has no succeeded run while the body names none. The
            detail of the second case is a fixed sentence.
    """
    resolved = _resolve_read_season(session, season)
    if resolved is None:
        raise HTTPException(status_code=404, detail="no succeeded simulation")

    row = session.execute(
        select(SimulationRunModel, SeasonModel.code)
        .join(SeasonModel, SeasonModel.id == SimulationRunModel.season_id)
        .where(
            SimulationRunModel.season_id == resolved.id,
            SimulationRunModel.status == "succeeded",
        )
        .order_by(
            SimulationRunModel.finished_at.desc().nulls_last(),
            SimulationRunModel.requested_at.desc(),
            SimulationRunModel.id.desc(),
        )
        .limit(1)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="no succeeded simulation")

    run, season_code = row
    return _detail(session, run, season_code)


@router.get("/history", responses=PROBLEM_RESPONSES)
def get_simulation_history(
    session: SessionDep,
    season: str | None = None,
    team_id: int | None = None,
) -> SimulationHistory:
    """Return one forecast point per as-of date of a season, ascending.

    A date contributes the latest succeeded run of that date and nothing else:
    when a day was requested twice and both attempts succeeded, the answer is the
    one that finished last, so the history is the series of final answers and not
    a series of attempts. A date without a succeeded run contributes no point, so
    the series is a record of forecasts and not a calendar.

    ``team_id`` narrows every point to one club, keeping the other columns of the
    point: a client tracks how one team's projected season moved from matchday to
    matchday. A date whose run does not mention the club keeps its point with an
    empty ``teams`` list, because the point is a stored forecast and dropping it
    would silently shorten the season.

    An omitted ``season`` selects the season that starts latest among the seasons
    with at least one run, the rule the collection and ``latest`` follow. A
    stored season without a run answers an empty history; a body that names no
    season while no season has a run is a 404, because the empty answer would
    otherwise describe a season the request never selected.

    The request issues at most four statements: the season probe, the team probe
    when a club is given, one statement for the runs and one for their team rows,
    whatever the number of points. Both joins are explicit, because the models
    declare no relationship.

    Raises:
        HTTPException: 404 when ``season`` names no stored season, when
            ``team_id`` names no stored team, or when neither is given and no
            season has a run. The details are fixed sentences.
    """
    resolved = _resolve_read_season(session, season)
    if resolved is None:
        raise HTTPException(status_code=404, detail="no simulation")
    if team_id is not None:
        _raise_if_team_is_unknown(session, team_id)

    latest_succeeded = (
        select(SimulationRunModel.id, SimulationRunModel.as_of_date)
        .where(
            SimulationRunModel.season_id == resolved.id,
            SimulationRunModel.status == "succeeded",
        )
        .ext(distinct_on(SimulationRunModel.as_of_date))
        .order_by(
            SimulationRunModel.as_of_date,
            SimulationRunModel.finished_at.desc().nulls_last(),
            SimulationRunModel.requested_at.desc(),
            SimulationRunModel.id.desc(),
        )
        .subquery()
    )
    runs = list(
        session.scalars(
            select(SimulationRunModel)
            .join(latest_succeeded, latest_succeeded.c.id == SimulationRunModel.id)
            .order_by(SimulationRunModel.as_of_date)
        )
    )

    teams_by_run: dict[uuid.UUID, list[SimulationHistoryTeam]] = {run.id: [] for run in runs}
    if teams_by_run:
        team_statement = (
            select(SimulationTeamResultModel, TeamModel.name)
            .join(TeamModel, TeamModel.id == SimulationTeamResultModel.team_id)
            .where(SimulationTeamResultModel.run_id.in_(list(teams_by_run)))
        )
        if team_id is not None:
            team_statement = team_statement.where(SimulationTeamResultModel.team_id == team_id)
        team_statement = team_statement.order_by(
            SimulationTeamResultModel.run_id,
            SimulationTeamResultModel.mean_position,
            SimulationTeamResultModel.mean_points.desc(),
            TeamModel.name,
            TeamModel.id,
        )
        for result, name in session.execute(team_statement).all():
            teams_by_run[result.run_id].append(_history_team(result, name))

    return SimulationHistory(
        season=resolved.code,
        points=[
            SimulationHistoryPoint(
                run_id=run.id,
                as_of_date=run.as_of_date,
                kind=cast(SimulationKind, run.kind),
                last_complete_matchday=run.last_complete_matchday,
                teams=teams_by_run[run.id],
            )
            for run in runs
        ],
    )


@router.get("/{run_id}", responses=PROBLEM_RESPONSES)
def get_simulation(run_id: uuid.UUID, session: SessionDep) -> SimulationDetail:
    """Return one stored run with the table it projected.

    ``table`` is in projected table order, the order the worker prints, and it is
    empty unless the run succeeded: a queued or failed run wrote no team rows, and
    reporting a partial table would let a client read an unfinished simulation as
    a finished one.

    A path segment that is not a UUID is refused by the request validation, so
    ``/simulations/latest`` and ``/simulations/history`` are declared before this
    route and are never read as identifiers.

    The request issues two statements, the run with its season and, when the run
    succeeded, the table. The season join is explicit because the models declare
    no relationship the payload could lazy-load.

    Raises:
        HTTPException: 404 when no stored run has ``run_id``. The detail is a
            fixed sentence and never repeats the identifier.
    """
    row = session.execute(
        select(SimulationRunModel, SeasonModel.code)
        .join(SeasonModel, SeasonModel.id == SimulationRunModel.season_id)
        .where(SimulationRunModel.id == run_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Simulation run not found")

    run, season_code = row
    return _detail(session, run, season_code)


def _stored_season(session: Session, code: str) -> SeasonModel:
    """Return the stored season ``code`` names.

    Raises:
        HTTPException: no season has ``code``. The detail is a fixed sentence and
            never repeats the value the client sent, and the season endpoints
            follow the same rule.
    """
    stored = session.scalars(select(SeasonModel).where(SeasonModel.code == code)).first()
    if stored is None:
        raise HTTPException(status_code=404, detail="Season not found")
    return stored


def _latest_season_with_matches(session: Session) -> SeasonModel:
    """Return the season that starts latest among the seasons with a match.

    This is the rule the standings endpoint uses, so a create request without a
    season lands on the season a dashboard opens on. Requiring a match keeps the
    answer on a season ingestion has loaded instead of a season that is only
    registered, which would let a request simulate an empty league.

    Raises:
        HTTPException: no season has a match. The detail is a fixed sentence.
    """
    latest = session.scalars(
        select(SeasonModel)
        .where(select(MatchModel.id).where(MatchModel.season_id == SeasonModel.id).exists())
        .order_by(SeasonModel.start_year.desc())
        .limit(1)
    ).first()
    if latest is None:
        raise HTTPException(status_code=404, detail="No season with matches")
    return latest


def _latest_season_with_runs(session: Session) -> SeasonModel | None:
    """Return the season that starts latest among the seasons with a run.

    A read endpoint answers with the season somebody has actually simulated, so an
    omitted season never selects a season whose runs are all absent. ``None``
    means no season has a run at all, which each endpoint reports its own way: an
    empty page, or its own missing-resource detail.
    """
    return session.scalars(
        select(SeasonModel)
        .where(
            select(SimulationRunModel.id)
            .where(SimulationRunModel.season_id == SeasonModel.id)
            .exists()
        )
        .order_by(SeasonModel.start_year.desc())
        .limit(1)
    ).first()


def _resolve_read_season(session: Session, season: str | None) -> SeasonModel | None:
    """Return the season a read endpoint answers about.

    A given code must name a stored season, so an unknown code is a 404 whatever
    the runs carry. An omitted code selects the season that starts latest among
    the seasons with a run.

    Raises:
        HTTPException: ``season`` is given and names no stored season.
    """
    if season is not None:
        return _stored_season(session, season)
    return _latest_season_with_runs(session)


def _raise_if_team_is_unknown(session: Session, team_id: int) -> None:
    """Raise the 404 of a team filter that names no stored team.

    It runs before the history is read, because an unknown club would otherwise
    answer every point with an empty team list, which reads like a season the club
    never played in.

    Raises:
        HTTPException: no team has ``team_id``.
    """
    stored_id = session.scalar(select(TeamModel.id).where(TeamModel.id == team_id))
    if stored_id is None:
        raise HTTPException(status_code=404, detail="Team not found")


def _mark_enqueue_failed(session: Session, run_id: uuid.UUID) -> bool:
    """Record on a still-queued run that the queue refused it, and commit.

    The update is conditional on the row still being ``queued``. A message that
    did reach the broker can be claimed before the enqueue call reports its
    error, and a claim that won that race is not overwritten: the worker owns the
    run, so a terminal failure would describe work that is still executing and
    would let the next identical request insert a second run for the same natural
    key.

    The status is terminal, so the row also needs the timestamps the schema ties
    to it: the enqueue instant is both its start and its finish, because the row
    never executed a single run and the schema has no terminal state that carries
    no start. The statement bypasses the session's copies of the row on purpose;
    the commit that follows expires them, so a later read sees the stored state.

    Returns:
        Whether exactly one ``queued`` row was updated. ``False`` means a worker
        claimed the row first, so the caller must describe the stored run instead
        of reporting a failure.
    """
    result = cast(
        CursorResult[Any],
        session.execute(
            update(SimulationRunModel)
            .where(
                SimulationRunModel.id == run_id,
                SimulationRunModel.status == "queued",
            )
            .values(
                status="failed",
                started_at=func.now(),
                finished_at=func.now(),
                error=ENQUEUE_FAILED_ERROR,
            )
            .execution_options(synchronize_session=False)
        ),
    )
    session.commit()
    return result.rowcount == 1


def _detail(session: Session, run: SimulationRunModel, season_code: str) -> SimulationDetail:
    """Return one run with its projected table, which only a succeeded run has."""
    table = _table_rows(session, run.id) if run.status == "succeeded" else []
    return SimulationDetail(run=_run_out(run, season_code), table=table)


def _table_rows(session: Session, run_id: uuid.UUID) -> list[SimulationTeamRow]:
    """Return the stored team rows of one run in projected table order.

    The order is the one the worker prints, so the API and the command line show
    the same ranking; the identifier closes it, so no two rows compare equal even
    when a club name and its mean position tie.
    """
    statement = (
        select(SimulationTeamResultModel, TeamModel.name)
        .join(TeamModel, TeamModel.id == SimulationTeamResultModel.team_id)
        .where(SimulationTeamResultModel.run_id == run_id)
        .order_by(
            SimulationTeamResultModel.mean_position,
            SimulationTeamResultModel.mean_points.desc(),
            TeamModel.name,
            TeamModel.id,
        )
    )
    return [_team_row(result, name) for result, name in session.execute(statement).all()]


def _run_out(run: SimulationRunModel, season_code: str) -> SimulationRun:
    """Return the payload of one stored run row, with the code of its season.

    The payload is built from the columns of the rows the statement selected,
    never from a relationship: the models declare none. The status, the kind, the
    trigger and the model are narrowed with a cast because the ORM types them as
    plain text; a check constraint restricts each to the values of its literal, so
    the cast states a rule the database already enforces.
    """
    return SimulationRun(
        id=run.id,
        season=season_code,
        as_of_date=run.as_of_date,
        n_runs=run.n_runs,
        seed=run.seed,
        model=cast(SimulationModel, run.model),
        model_version=run.model_version,
        status=cast(SimulationStatus, run.status),
        kind=cast(SimulationKind, run.kind),
        trigger=cast(SimulationTrigger, run.trigger),
        requested_at=run.requested_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
        n_fixed=run.n_fixed,
        n_simulated=run.n_simulated,
        last_complete_matchday=run.last_complete_matchday,
        qualification=SimulationQualification(
            ucl_positions=run.ucl_positions,
            europe_positions=run.europe_positions,
            relegation_positions=run.relegation_positions,
        ),
    )


def _team_row(result: SimulationTeamResultModel, name: str) -> SimulationTeamRow:
    """Return the payload of one stored team row of a projected table.

    ``points_pmf`` is stored as the probability of each reachable total starting
    at ``points_min``, which is exactly the ``min``-relative form the payload
    uses, so the array is passed through unchanged and ``min`` names its first
    entry. ``position_probs`` already has one entry per team of the league, index
    0 for first place, so it passes through unchanged too.
    """
    return SimulationTeamRow(
        team=Team(id=result.team_id, name=name),
        current=SimulationCurrent(
            played=result.current_played,
            points=result.current_points,
            goal_difference=result.current_goal_difference,
        ),
        expected_points=result.mean_points,
        points_quantiles=SimulationPointsQuantiles(
            p05=result.points_p05,
            p25=result.points_p25,
            p50=result.points_p50,
            p75=result.points_p75,
            p95=result.points_p95,
        ),
        points_distribution=SimulationPointsDistribution(
            min=result.points_min,
            probabilities=list(result.points_pmf),
        ),
        expected_position=result.mean_position,
        position_probabilities=list(result.position_probs),
        p_title=result.p_title,
        p_ucl=result.p_ucl,
        p_europe=result.p_europe,
        p_relegation=result.p_relegation,
    )


def _history_team(result: SimulationTeamResultModel, name: str) -> SimulationHistoryTeam:
    """Return the payload of one team row inside one point of the history.

    Only the columns the history compares across as-of dates are carried, so a
    point stays small whatever the number of teams and dates.
    """
    return SimulationHistoryTeam(
        team=Team(id=result.team_id, name=name),
        p_title=result.p_title,
        p_ucl=result.p_ucl,
        p_europe=result.p_europe,
        p_relegation=result.p_relegation,
        expected_points=result.mean_points,
        expected_position=result.mean_position,
    )
