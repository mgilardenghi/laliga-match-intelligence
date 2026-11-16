"""Synthetic simulation rows and a queue fake for the simulation endpoint tests.

Everything inserted here is invented: the club names, the season codes, the run
counters and the projected tables are made up, nothing is downloaded, simulated
or queued, and no crest or logo is involved. The seasons default to 2098-99, a
range no data source covers, so a row stays clear of the seeded reference seasons
and a test can assert on its own runs only.

The rows are written directly instead of by executing a simulation, because the
endpoints this module serves only read what the worker stored: a test that ran
the engine would take minutes and would depend on a fitted model to prove a
payload shape.

Every helper inserts through the ``Session`` it is given and flushes, never
commits: the caller's transaction is the only thing that decides whether the rows
survive, and the flush is what fills the generated identifiers the test asserts
on. A test that examines a run the create endpoint wrote reads the row the route
itself committed, which is the one exception and is what makes the 202 body
checkable against the stored state.
"""

import datetime
import uuid

from lmi_core.db.models import Season, SimulationRun, SimulationTeamResult, Team
from sqlalchemy.orm import Session

from tests.factories import create_season, create_team

DEFAULT_AS_OF_DATE = datetime.date(2098, 10, 1)
"""The as-of date of a synthetic run: mid-season, so the cut reads plausibly."""

DEFAULT_REQUESTED_AT = datetime.datetime(2098, 10, 1, 9, 0, tzinfo=datetime.UTC)
"""The request instant of a synthetic run, which the order tests replace."""

DEFAULT_FINISHED_AFTER = datetime.timedelta(hours=1)
"""How long after its start a synthetic terminal run finished."""

TEAM_NAMES = ("Test FC Beta", "Test FC Gamma", "Test FC Delta")
"""Invented club names, unique in the table and unlike any real club."""

QUALIFICATION = (4, 6, 3)
"""The continental and relegation thresholds a synthetic run was requested with."""


class FakeQueue:
    """A producer that records the identifiers the API hands it.

    ``attempts`` holds every identifier in the order it arrived, whether or not
    the call then failed, so a test tells "the API did not enqueue" from "the API
    enqueued and the broker refused". ``failure`` makes the next calls raise it,
    which is how the 503 path is exercised without a broker.

    The class satisfies the ``SimulationQueue`` protocol structurally, so the
    route dependency override accepts it and no Redis is needed anywhere in the
    suite.
    """

    def __init__(self) -> None:
        self.attempts: list[uuid.UUID] = []
        self.failure: Exception | None = None

    def enqueue(self, run_id: uuid.UUID) -> None:
        """Record ``run_id`` and raise the configured failure, if one is set."""
        self.attempts.append(run_id)
        if self.failure is not None:
            raise self.failure


def create_simulation_season(
    session: Session,
    *,
    code: str = "2098-99",
    start_year: int = 2098,
    team_names: tuple[str, ...] = TEAM_NAMES,
) -> tuple[Season, list[Team]]:
    """Insert one synthetic season with its clubs and return both.

    The season and the clubs are the two rows a simulation references, so a test
    that only needs a stored run still gets a complete object graph without
    touching the shared seeded seasons.
    """
    season = create_season(session, code=code, start_year=start_year)
    teams = [create_team(session, name=name) for name in team_names]
    return season, teams


def create_simulation_run(
    session: Session,
    *,
    season: Season,
    status: str = "queued",
    as_of_date: datetime.date = DEFAULT_AS_OF_DATE,
    n_runs: int = 1000,
    seed: int = 0,
    kind: str = "live",
    trigger: str = "api",
    requested_at: datetime.datetime = DEFAULT_REQUESTED_AT,
    started_at: datetime.datetime | None = None,
    finished_at: datetime.datetime | None = None,
    last_complete_matchday: int | None = None,
    n_fixed: int | None = None,
    n_simulated: int | None = None,
    model_version: str | None = None,
    error: str | None = None,
) -> SimulationRun:
    """Insert one simulation run and return the ORM row with its identifier.

    ``requested_at`` is a parameter instead of a default the caller cannot
    change, because a test that asserts on the newest-first order has to write the
    instants it expects to read back.

    The two timestamps follow from ``status``, because the schema ties them to it
    with the ``queued_has_no_start`` and ``finished_matches_status`` checks: a
    queued run carries neither, a running run carries only its start, and a
    terminal run carries both.

    Raises:
        ValueError: the caller contradicts ``status`` with a timestamp, because
            the check constraint would reject the row and the flush would fail
            with a database error instead of the reason.
    """
    if status == "queued":
        if started_at is not None or finished_at is not None:
            raise ValueError("a queued simulation run carries no start and no finish")
    elif status == "running":
        if finished_at is not None:
            raise ValueError("a running simulation run has no finished_at stamp")
        if started_at is None:
            started_at = requested_at
    else:
        if started_at is None:
            started_at = requested_at
        if finished_at is None:
            finished_at = started_at + DEFAULT_FINISHED_AFTER

    ucl_positions, europe_positions, relegation_positions = QUALIFICATION
    row = SimulationRun(
        season_id=season.id,
        as_of_date=as_of_date,
        n_runs=n_runs,
        seed=seed,
        model="dixon_coles",
        model_version=model_version,
        ucl_positions=ucl_positions,
        europe_positions=europe_positions,
        relegation_positions=relegation_positions,
        kind=kind,
        trigger=trigger,
        status=status,
        requested_at=requested_at,
        started_at=started_at,
        finished_at=finished_at,
        code_version="test",
        n_fixed=n_fixed,
        n_simulated=n_simulated,
        last_complete_matchday=last_complete_matchday,
        error=error,
    )
    session.add(row)
    session.flush()
    return row


def create_team_result(
    session: Session,
    *,
    run: SimulationRun,
    team: Team,
    mean_position: float,
    mean_points: float,
    current_played: int = 0,
    current_points: int = 0,
    current_goal_difference: int = 0,
    points_min: int = 40,
    points_pmf: list[float] | None = None,
    points_p05: int = 30,
    points_p25: int = 40,
    points_p50: int = 45,
    points_p75: int = 50,
    points_p95: int = 60,
    position_probs: list[float] | None = None,
    p_title: float = 0.0,
    p_ucl: float = 0.0,
    p_europe: float = 0.0,
    p_relegation: float = 0.0,
) -> SimulationTeamResult:
    """Insert one team's projected season inside ``run`` and return the row.

    ``mean_position`` and ``mean_points`` are required, because every ordering
    assertion of the detail and the history needs to write the values it expects
    to read back. ``points_pmf`` is the probability of each total starting at
    ``points_min`` and ``position_probs`` one entry per team, index 0 for first
    place, which is the shape the engine stores and the API returns unchanged.
    """
    row = SimulationTeamResult(
        run_id=run.id,
        team_id=team.id,
        current_played=current_played,
        current_points=current_points,
        current_goal_difference=current_goal_difference,
        mean_points=mean_points,
        points_p05=points_p05,
        points_p25=points_p25,
        points_p50=points_p50,
        points_p75=points_p75,
        points_p95=points_p95,
        points_min=points_min,
        points_pmf=points_pmf if points_pmf is not None else [0.5, 0.5],
        mean_position=mean_position,
        position_probs=position_probs if position_probs is not None else [0.5, 0.5],
        p_title=p_title,
        p_ucl=p_ucl,
        p_europe=p_europe,
        p_relegation=p_relegation,
    )
    session.add(row)
    session.flush()
    return row
