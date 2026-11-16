"""Simulation payloads: the stored scenario runs and the tables they project.

A simulation run is a request to play out the rest of one season many times. The
API answers it with a stored row that moves through ``queued``, ``running`` and
one terminal state, and the worker later attaches one row per team of the
league. :class:`SimulationRun` describes the request row, :class:`SimulationTeamRow`
one team of the projected table, and :class:`SimulationDetail` pairs the two.

:class:`SimulationRequest` is the only body a client sends. Its bounds are
expressed on the fields, so a value outside them is refused by the request
validation of the whole API instead of by a branch in the endpoint.

Several nulls carry meaning and none of them means zero. ``model_version`` is
null until the worker has fitted the model, ``started_at`` is null while the run
is queued, and ``finished_at`` is null while the run is active. ``n_fixed``,
``n_simulated`` and ``last_complete_matchday`` are null for the same reason: a
run that has not executed yet has counted nothing, which is not the same as
having counted zero.
"""

import datetime
import uuid
from typing import Final, Literal

from pydantic import BaseModel, Field

from lmi_api.schemas.common import Page
from lmi_api.schemas.teams import Team

SimulationModel = Literal["dixon_coles"]
"""The only model a simulation run can use.

The database stores the same spelling in a text column guarded by a check
constraint, and the literal repeats it so a generated client models the model as
an enumeration instead of a free string.
"""

SimulationStatus = Literal["queued", "running", "succeeded", "failed"]
"""The four states a run can be in.

``queued`` means the run holds its natural key and waits for a worker, which is
why such a run carries no ``started_at``. ``running`` means a worker claimed it.
``succeeded`` and ``failed`` are terminal and carry a ``finished_at``, and check
constraints keep the timestamps and the status in agreement in both directions.
"""

SimulationKind = Literal["live", "backfill"]
"""What the run is for: the season in progress, or a retroactive cut of the past."""

SimulationTrigger = Literal["api", "cli", "schedule"]
"""What asked for the run: this API, the worker command line, or the daily beat."""

DEFAULT_N_RUNS = 10_000
"""How many seasons a run simulates when the client does not say."""

MIN_N_RUNS = 1_000
"""The smallest run count the API accepts."""

MAX_N_RUNS = 50_000
"""The largest run count the API accepts.

The schema itself accepts up to 100,000. The API stops at half of that because a
larger request keeps one CPU busy for minutes and the endpoint is meant to be a
prompt answer, not a batch job.
"""

DEFAULT_SEED = 0
"""The seed a request uses when the client does not say, so a repeat is reproducible."""

MAX_SEED: Final = 2**63 - 1
"""The largest seed the ``simulation_runs.seed`` BIGINT column can store."""


class SimulationRequest(BaseModel):
    """What a client asks the simulator to run.

    Every field is optional: the season defaults to the one the season endpoints
    open on, the as-of date defaults to the Madrid date, the run count to
    :data:`DEFAULT_N_RUNS` and the seed to :data:`DEFAULT_SEED`.

    ``as_of_date`` is the last date whose played matches count as fixed results.
    The endpoint refuses a date later than the Madrid date, because a simulation
    of matches whose results are not known yet would be a forecast dressed as a
    table. ``n_runs`` and ``seed`` carry their bounds on the fields, so a value
    outside them is a validation failure of the whole API and never reaches the
    endpoint.
    """

    season: str | None = None
    as_of_date: datetime.date | None = None
    n_runs: int = Field(default=DEFAULT_N_RUNS, ge=MIN_N_RUNS, le=MAX_N_RUNS)
    seed: int = Field(default=DEFAULT_SEED, ge=0, le=MAX_SEED)


class SimulationQualification(BaseModel):
    """Where the continental and relegation places of the run start.

    ``ucl_positions`` and ``europe_positions`` count places from the top, so a
    team qualifies when its final position is at most that number;
    ``relegation_positions`` counts places from the bottom. The thresholds are
    stored with the run, because a change of the competition format must not
    rewrite what a past run meant.
    """

    ucl_positions: int
    europe_positions: int
    relegation_positions: int


class SimulationRun(BaseModel):
    """One stored simulation request.

    ``season`` is the code of the season the run simulates, spelled the way the
    season endpoints spell it, and ``as_of_date`` is the last date whose played
    matches the run may count as fixed. ``n_runs`` and ``seed`` are the size and
    the seed of the Monte Carlo draw, and together with the season, the as-of
    date and the model they are the run's natural key while it is active.

    ``qualification`` repeats the thresholds the run was requested with, so a
    client reads the meaning of ``p_ucl``, ``p_europe`` and ``p_relegation``
    from the same payload instead of assuming today's competition format.
    """

    id: uuid.UUID
    season: str
    as_of_date: datetime.date
    n_runs: int
    seed: int
    model: SimulationModel
    model_version: str | None
    status: SimulationStatus
    kind: SimulationKind
    trigger: SimulationTrigger
    requested_at: datetime.datetime
    started_at: datetime.datetime | None
    finished_at: datetime.datetime | None
    error: str | None
    n_fixed: int | None
    n_simulated: int | None
    last_complete_matchday: int | None
    qualification: SimulationQualification


class SimulationCurrent(BaseModel):
    """The real standing of one team at the run's as-of date.

    These are the played matches the run counted as fixed results, so a client
    compares the current column with the projected one on the same scale.
    """

    played: int
    points: int
    goal_difference: int


class SimulationPointsQuantiles(BaseModel):
    """The quantiles of one team's final points total.

    Each value is a total the run reached, not an offset, so it is directly
    comparable with ``current.points`` and ``expected_points``.
    """

    p05: int
    p25: int
    p50: int
    p75: int
    p95: int


class SimulationPointsDistribution(BaseModel):
    """The full distribution of one team's final points total.

    ``min`` is the lowest total the run reached, which is the value the first
    entry of ``probabilities`` belongs to: entry ``i`` is the probability of
    finishing on ``min + i`` points, so the array covers every total between its
    ends and no gap. The entries sum to one within floating-point tolerance.
    """

    min: int
    probabilities: list[float]


class SimulationTeamRow(BaseModel):
    """One team of a run's projected table.

    ``team`` is the club, ``current`` its real standing at the as-of date and the
    rest is what the simulation says about it. ``expected_points`` is the mean
    final total and ``expected_position`` the mean final position, which is not
    necessarily an integer and is not the position of the row in the table.

    ``position_probabilities`` has one entry per team of the league: index
    ``i`` is the probability of finishing at position ``i + 1``, so index 0 is
    first place. It therefore sums to one, like ``points_distribution``.
    """

    team: Team
    current: SimulationCurrent
    expected_points: float
    points_quantiles: SimulationPointsQuantiles
    points_distribution: SimulationPointsDistribution
    expected_position: float
    position_probabilities: list[float]
    p_title: float
    p_ucl: float
    p_europe: float
    p_relegation: float


class SimulationDetail(BaseModel):
    """One run with the table it projected.

    ``table`` is in projected table order: mean final position ascending, then
    mean points descending, then the club name and its identifier. It is empty
    unless ``run.status`` is ``succeeded``, because a run that has not executed
    has written no team rows; an empty table is therefore never a claim that the
    league has no teams.
    """

    run: SimulationRun
    table: list[SimulationTeamRow]


class SimulationRunPage(Page[SimulationRun]):
    """One page of stored runs, most recently requested first.

    The generic envelope is specialised here so the generated client and the
    OpenAPI document name a concrete schema, ``SimulationRunPage``, instead of an
    anonymous generic.
    """


class SimulationHistoryTeam(BaseModel):
    """One team in one point of the history.

    Only the summary of the team's projected season is kept: the history exists
    to show how a forecast moved from matchday to matchday, so it carries the
    small set of numbers that can be compared across dates. The full row of a
    single run is :class:`SimulationTeamRow`, served by the detail endpoint.
    """

    team: Team
    p_title: float
    p_ucl: float
    p_europe: float
    p_relegation: float
    expected_points: float
    expected_position: float


class SimulationHistoryPoint(BaseModel):
    """The forecast of one as-of date.

    ``run_id`` names the run the numbers come from: the latest succeeded run of
    that date, so a date that was simulated more than once yields its last
    answer and not an average of several. ``kind`` tells a live forecast from a
    retroactive one, because a backfilled date answers "what would the season
    have looked like then", not "what did we say then".

    ``teams`` is in projected table order, filtered to the caller's team when it
    asked for one. A team that a run does not mention is absent from the point
    rather than present with zero probabilities.
    """

    run_id: uuid.UUID
    as_of_date: datetime.date
    kind: SimulationKind
    last_complete_matchday: int | None
    teams: list[SimulationHistoryTeam]


class SimulationHistory(BaseModel):
    """The forecast history of one season, one point per as-of date.

    ``points`` ascends by ``as_of_date``, so a client draws the series in the
    order the season happened. A date contributes one point, and a date the
    simulator never succeeded on contributes none: the history is the record of
    stored answers and not a calendar.
    """

    season: str
    points: list[SimulationHistoryPoint]
