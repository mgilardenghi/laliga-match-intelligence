"""Relegation-race payloads: one club's survival race in one season.

The race is three stored things read together: the club and its remaining
fixtures, the historical survival line of the league, and the club's row in the
latest succeeded simulation of the season with the clubs it is racing. No rule
of any of them is restated here. The survival line is
:class:`lmi_core.analytics.relegation.SurvivalThreshold` mapped field for field,
the rival group is :func:`lmi_core.analytics.relegation.nearest_rivals`, the
table positions are :func:`lmi_core.standings.compute_standings`, and the season
series is :class:`~lmi_api.schemas.simulations.SimulationHistory`, reused rather
than redeclared, so the race and the history endpoint cannot serve one series in
two shapes.

:class:`SurvivalThreshold` repeats the core class name on purpose. The core
class is the sample a rule computed; this one is the payload shape a client
validates, and the two are kept apart so a change of the served fields never
changes what the rule returns.

Three nulls carry meaning and none of them means zero. ``run_id`` and
``as_of_date`` are null when the season has no succeeded simulation, because a
race without a simulation has no run to name rather than a run that measured
nothing. ``opponent_position`` is null for an opponent the season's table does
not rank. ``p_win``, ``p_draw``, ``p_loss`` and ``next_match`` are null because
this milestone stores no per-fixture or next-match simulation output at all:
they are declared nullable so a stored simulation can fill them without a change
to this contract, and today they are always null.
"""

import datetime
import uuid

from pydantic import BaseModel

from lmi_api.schemas.matches import MatchSummary
from lmi_api.schemas.simulations import SimulationHistory
from lmi_api.schemas.team_season_stats import Venue
from lmi_api.schemas.teams import Team

__all__ = [
    "NextMatch",
    "NextMatchOutcome",
    "NextMatchOutcomes",
    "RelegationRace",
    "RelegationRival",
    "RemainingFixture",
    "SurvivalThreshold",
]


class SurvivalThreshold(BaseModel):
    """The historical survival line of the league, as the payload serves it.

    ``points_median`` is the median final total of the counted place over the
    counted seasons and is never rounded, while ``points_min`` and
    ``points_max`` are the extremes of the same sample. ``seasons_used`` makes
    the sample size visible, so a median of one season is not read as a median
    of ten, and ``position`` names the place the sample was read from.
    """

    points_median: float
    points_min: int
    points_max: int
    seasons_used: int
    position: int


class RelegationRival(BaseModel):
    """One club of the race, with the stored numbers the group compares.

    ``team`` is the club, ``current_points`` its real total at the run's as-of
    date, and the rest are the run's projection of it: the mean final total, the
    relegation probability and the low and high ends of the projected total.
    Every number is a stored column of that one run, so no value here is
    re-simulated or recomputed on read.
    """

    team: Team
    current_points: int
    expected_points: float
    p_relegation: float
    points_p05: int
    points_p95: int


class RemainingFixture(BaseModel):
    """One fixture the club still has to play, with what is known about it.

    ``venue`` is where the club plays it, and ``opponent_position`` the
    opponent's place in the season's table, or null for a club the table does
    not rank. ``p_win``, ``p_draw`` and ``p_loss`` are the per-fixture
    simulation outputs the run will store, and are null today: the fields exist
    so a stored simulation fills them without a contract change, not because the
    numbers are currently computed and withheld.
    """

    match: MatchSummary
    venue: Venue
    opponent_position: int | None
    p_win: float | None
    p_draw: float | None
    p_loss: float | None


class NextMatchOutcome(BaseModel):
    """What one outcome of the next match would do to the club's projected season.

    The four probabilities and the expected total are what the next-match
    simulation of the run reports for that outcome, so a client compares a win,
    a draw and a loss on one scale instead of reading one projected table.
    """

    p_outcome: float
    p_relegation: float
    p_europe: float
    p_title: float
    expected_points: float


class NextMatchOutcomes(BaseModel):
    """The three outcomes of the next match, each with its projection.

    Every outcome is present, because the value of the block is the comparison:
    a client that lacks one of the three cannot say what a draw would cost.
    """

    win: NextMatchOutcome
    draw: NextMatchOutcome
    loss: NextMatchOutcome


class NextMatch(BaseModel):
    """The club's next fixture and the projection of each of its outcomes.

    ``match_id`` names the fixture the outcomes belong to, so a client links to
    the match endpoint without matching on a date, and the outcomes nest under
    it so one response carries both.
    """

    match_id: int
    outcomes: NextMatchOutcomes


class RelegationRace(BaseModel):
    """One club's relegation race in one season.

    ``survival_threshold`` is the historical survival line the club is measured
    against, ``rivals`` the club and the nearest clubs of the latest succeeded
    simulation, ``series`` the run history of exactly those clubs, and
    ``remaining_fixtures`` the club's fixtures still to play. ``run_id`` and
    ``as_of_date`` name the simulation the rivals and the series come from and
    are null when the season has none.

    ``next_match`` is null in this milestone. Its shape is already here, so the
    day the run stores the per-outcome projections they are served in this
    payload instead of in a new resource.
    """

    team: Team
    season: str
    run_id: uuid.UUID | None
    as_of_date: datetime.date | None
    survival_threshold: SurvivalThreshold
    rivals: list[RelegationRival]
    series: SimulationHistory
    remaining_fixtures: list[RemainingFixture]
    next_match: NextMatch | None
