"""Synthetic simulation runs and team results for the team-focus tests.

Everything inserted here is invented: the run counters, the seed, the model
version and every simulated number are made up, nothing is simulated and no
source is contacted. The helpers write the shape the worker writes, so a test
reads back what a finished run of the real pipeline would have stored.

A simulation run cannot be inserted like any other row either. The table ties
``status`` to the three timestamps with two check constraints: ``queued`` has no
``started_at``, ``queued`` and ``running`` have no ``finished_at``, and a
terminal state carries both. The helpers therefore derive the stamps from the
status instead of letting a test build a row the database would reject, and they
take ``finished_at`` explicitly because the order a focus view reads runs in is
decided by it.

Every helper flushes and never commits: the caller's transaction is the only
thing that decides whether a row survives, and the flush is what fills the
generated identifier the test asserts on.
"""

import datetime

from lmi_core.db.models import Season, SimulationRun, SimulationTeamResult, Team
from sqlalchemy.orm import Session

from tests.live_support import madrid_today

_POSITIONS = 20
"""How many teams a Spanish top-flight table holds, so the position array has its length."""


def create_simulation_run(
    session: Session,
    *,
    season: Season,
    status: str = "succeeded",
    as_of_date: datetime.date | None = None,
    n_runs: int = 1000,
    seed: int = 0,
    kind: str = "live",
    trigger: str = "cli",
    ucl_positions: int = 4,
    europe_positions: int = 6,
    relegation_positions: int = 3,
    finished_at: datetime.datetime | None = None,
    model_version: str = "1.0",
) -> SimulationRun:
    """Insert one simulation run and return the ORM row with its identifier.

    ``as_of_date`` defaults to the Madrid date of the database, which is the date
    the service stamps on a live run; a test that asserts on the order of
    several runs passes its own dates.

    ``finished_at`` is the sort key of the focus outlook, so a test that stores
    more than one succeeded run has to write the instants it expects to read
    back. A terminal run finished at the instant it was created when the caller
    passes nothing, and the stamps of a ``queued`` or ``running`` run follow the
    status: a queued run has not started and a running one has no finish.

    The defaults satisfy every check constraint of the table: ``n_runs`` is
    inside 100 to 100000, ``seed`` is not negative, the three qualification
    counts are ordered, and the model is the only one the table supports.

    Raises:
        ValueError: ``status`` is ``queued`` or ``running`` and ``finished_at``
            is given, because the check constraint would reject the row and the
            flush would fail with a database error instead of the reason.
    """
    requested_at = datetime.datetime.now(datetime.UTC)
    started_at: datetime.datetime | None
    if status in ("queued", "running"):
        if finished_at is not None:
            raise ValueError(f"a {status} simulation run has no finished_at stamp")
        started_at = None if status == "queued" else requested_at
    else:
        if finished_at is None:
            finished_at = requested_at
        started_at = requested_at

    row = SimulationRun(
        season_id=season.id,
        as_of_date=as_of_date if as_of_date is not None else madrid_today(session),
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
    )
    session.add(row)
    session.flush()
    return row


def create_team_result(
    session: Session,
    *,
    run: SimulationRun,
    team: Team,
    mean_points: float = 50.0,
    mean_position: float = 8.0,
    points_p05: int = 40,
    points_p95: int = 60,
    p_title: float = 0.1,
    p_ucl: float = 0.4,
    p_europe: float = 0.5,
    p_relegation: float = 0.01,
    current: tuple[int, int, int] = (10, 15, 3),
) -> SimulationTeamResult:
    """Insert one team's simulated season inside ``run`` and return the row.

    ``current`` is the team's standing before the simulated matches, as
    ``(played, points, goal_difference)``. The two derived arrays the table
    stores are written flat and uniform: ``points_pmf`` spreads the mass over the
    points range between ``points_p05`` and ``points_p95`` and ``position_probs``
    spreads it over every place of the table. Neither is read by the focus view,
    which serves the mean, the two quantiles and the four probabilities, so a
    made-up distribution costs the test nothing and keeps the columns non-null as
    the schema requires.

    The middle quantiles are derived from the two the caller passes, so
    ``points_p05 <= points_p25 <= points_p50 <= points_p75 <= points_p95`` holds
    for any ordered pair.
    """
    played, points, goal_difference = current
    span = points_p95 - points_p05 + 1
    distance = points_p95 - points_p05
    row = SimulationTeamResult(
        run_id=run.id,
        team_id=team.id,
        current_played=played,
        current_points=points,
        current_goal_difference=goal_difference,
        mean_points=mean_points,
        points_p05=points_p05,
        points_p25=points_p05 + distance // 4,
        points_p50=points_p05 + distance // 2,
        points_p75=points_p05 + 3 * distance // 4,
        points_p95=points_p95,
        points_min=points_p05,
        points_pmf=[1.0 / span] * span,
        mean_position=mean_position,
        position_probs=[1.0 / _POSITIONS] * _POSITIONS,
        p_title=p_title,
        p_ucl=p_ucl,
        p_europe=p_europe,
        p_relegation=p_relegation,
    )
    session.add(row)
    session.flush()
    return row
