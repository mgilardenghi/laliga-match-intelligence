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
not rank. ``p_win``, ``p_draw``, ``p_loss`` and ``next_match`` are the stored
outputs of the latest succeeded run of the season and are null when that run did
not store them: a run created before revision 0004 holds no per-fixture or
next-match detail, a fixture the run did not simulate has no probability, and
``next_match`` stays null under the freshness and never-sampled rules of
:func:`lmi_core.analytics.relegation.next_match_swing` and when the run holds no
row for the club. A probability the run never measured is served as unknown,
never as zero, which is what the nulls say.
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
    "NextMatchCurrent",
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
    not rank. ``p_win``, ``p_draw`` and ``p_loss`` are the run's probabilities of
    the three results from the club's side: at home the stored ``p_home``,
    ``p_draw`` and ``p_away``, away the same triple read from the other end, so
    ``p_win`` always describes the club and not the venue. They come from the
    latest succeeded run of the season and are null when that run did not store
    the fixture, which is the case for a run created before revision 0004 and for
    a fixture the run did not simulate.
    """

    match: MatchSummary
    venue: Venue
    opponent_position: int | None
    p_win: float | None
    p_draw: float | None
    p_loss: float | None


class NextMatchOutcome(BaseModel):
    """What one outcome of the next match would do to the club's projected season.

    The four probabilities and the expected total are what the latest succeeded
    run of the season measured for that outcome, so a client compares a win, a
    draw and a loss on one scale instead of reading one projected table.
    ``p_outcome`` is the share of the simulated runs that produced the outcome,
    while ``p_relegation``, ``p_europe`` and ``p_title`` are the shares of those
    same runs that ended each way; ``expected_points`` is their mean final total.
    Because ``p_outcome`` is a sampled frequency, it may differ slightly -- about
    half a percentage point at 10 000 runs -- from the model probability the same
    run stores for the fixture.
    """

    p_outcome: float
    p_relegation: float
    p_europe: float
    p_title: float
    expected_points: float


class NextMatchCurrent(BaseModel):
    """The club's own row of the same run the next-match swing comes from.

    ``p_relegation``, ``p_europe`` and ``p_title`` are the run's probabilities for
    the club over the whole remaining season, and ``expected_points`` is its
    expected final total -- the value ``rivals[].expected_points`` already
    carries. Reading these four numbers and the swing from one run is what lets a
    client compute every delta of the block against one consistent baseline
    instead of mixing two runs' projections.
    """

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
    it so one response carries both. ``current`` is the club's row of the same
    run, so every outcome is compared against the baseline the run measured
    rather than against a second simulation.

    The block comes from the latest succeeded run of the season and is null when
    that run did not store it: a run created before revision 0004 has no
    next-match detail, a stored match that is no longer the club's next fixture
    is answered as unknown by
    :func:`lmi_core.analytics.relegation.next_match_swing`, a result the run never
    sampled has no conditional probability, and a run without a row for the club
    has no ``current`` to compare against.
    """

    match_id: int
    outcomes: NextMatchOutcomes
    current: NextMatchCurrent


class RelegationRace(BaseModel):
    """One club's relegation race in one season.

    ``survival_threshold`` is the historical survival line the club is measured
    against, ``rivals`` the club and the nearest clubs of the latest succeeded
    simulation, ``series`` the run history of exactly those clubs, and
    ``remaining_fixtures`` the club's fixtures still to play. ``run_id`` and
    ``as_of_date`` name the simulation the rivals and the series come from and
    are null when the season has none.

    ``next_match`` is the club's next fixture with what each of its results does
    to the race, read from the same run as the rivals and the series. It is null
    when that run stored no next-match detail for the club, or answered unknown
    for it: an absent block says the run did not measure it, not that the club
    has no fixture left.
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
