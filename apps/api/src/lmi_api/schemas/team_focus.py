"""Team-focus payloads: one team's season as a single read.

The team, its fixtures and, when a simulation has succeeded, the stored outlook
of the season are rows served as they are stored. Everything else is derived on
read, and no rule of it is restated here: ``standing`` is the team's row of
:func:`lmi_core.standings.compute_standings`, ``form`` and ``splits`` count the
played matches of the season under the same win/draw/loss arithmetic, and the
``evaluation`` of an upcoming prediction comes from
:func:`lmi_core.live.scoring.evaluate`. A correction to one of those rules
therefore reaches this resource without a change to these shapes.

:class:`FormSummary` is shared with the match preview, which nests the recent
form of both sides, so it lives here rather than in the preview module and is
exported from ``__all__``.

Two nulls carry meaning and neither means "zero". ``points_per_game`` is null
for a side that played nothing, because an average over no game does not exist.
``outlook`` is null when the season has no succeeded simulation, or when that
run stored no row for the team. ``standing`` is nullable because the payload is
also the shape a read over a partial table would return; the team-set rule of
the season makes it non-null for every team this resource serves, so a null
there is a broken read rather than a state of the data.
"""

import datetime
import uuid

from pydantic import BaseModel

from lmi_api.schemas.matches import MatchSummary
from lmi_api.schemas.predictions import PredictionBrief
from lmi_api.schemas.standings import StandingForm, StandingRow
from lmi_api.schemas.teams import Team

__all__ = [
    "FormSummary",
    "SimulationOutlook",
    "SplitRecord",
    "TeamFocus",
    "TeamSplits",
    "UpcomingMatch",
]


class SplitRecord(BaseModel):
    """The counters of one group of matches seen from one team's side.

    The record is used for the home and the away half of a season, so every
    number covers only the matches of that group. ``points_per_game`` is
    ``points / played`` and is null, never ``0.0``, when the group is empty: a
    side that has not played a match has no average, and a zero would read as a
    side that played and earned nothing.
    """

    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    points: int
    points_per_game: float | None


class FormSummary(BaseModel):
    """The recent form of one team, with the matches it was read from.

    ``results`` holds one letter per match, newest first: ``W``, ``D`` or ``L``
    from this team's point of view. ``matches`` is the same matches in the same
    order, so a client can render the result next to the fixture it came from
    without a second request. ``results`` and ``matches`` therefore always have
    the same length.

    ``points``, ``goals_for`` and ``goals_against`` are the totals over exactly
    these matches, not over the season: the caller decides which matches the
    summary covers, so the same shape serves the last five fixtures of a season
    and the fixtures before a given kickoff.
    """

    team: Team
    results: list[StandingForm]
    points: int
    goals_for: int
    goals_against: int
    matches: list[MatchSummary]


class TeamSplits(BaseModel):
    """The season of one team split by venue.

    ``home`` counts the matches the team played as the home side and ``away``
    the ones it played as the visitor. The two records are kept apart because
    home advantage is real enough that a pooled number hides the side that is
    failing.
    """

    home: SplitRecord
    away: SplitRecord


class UpcomingMatch(BaseModel):
    """One fixture still to be played, with the stored forecasts of it.

    ``predictions`` holds one brief per model and is empty when no model has a
    stored forecast for the fixture yet, which is the normal state of a match
    several days out. The fixture is nested rather than referenced so a client
    renders the clubs, the date and the probabilities from one response.
    """

    match: MatchSummary
    predictions: list[PredictionBrief]


class SimulationOutlook(BaseModel):
    """One team's row in the latest succeeded simulation of the season.

    Every number is a stored column of that run: the four qualification
    probabilities, ``expected_points`` (the stored ``mean_points``),
    ``expected_position`` (the stored ``mean_position``) and the two stored
    points quantiles. Nothing is re-simulated and nothing is recomputed on read,
    so the outlook of a run never changes after the run finished, and the
    ``run_id`` is the trace back to the full table of that run.

    ``points_p05`` and ``points_p95`` are the 5th and 95th percentile of the
    simulated final points total, which is why they are whole points while the
    expected values are not.
    """

    run_id: uuid.UUID
    as_of_date: datetime.date
    p_title: float
    p_ucl: float
    p_europe: float
    p_relegation: float
    expected_points: float
    points_p05: int
    points_p95: int
    expected_position: float


class TeamFocus(BaseModel):
    """Everything one team's focus view serves about one season.

    ``season`` is the season code the other endpoints accept as their filter.
    ``standing`` is the team's row of the season table, ``form`` its last five
    played matches of the season and ``splits`` all of them by venue, so the
    three describe the same matches from three angles. ``upcoming`` are the next
    fixtures of the season with the forecasts stored for them, and ``outlook``
    is the simulated season of the latest succeeded run.

    The scope is deliberately the season: a form string that mixed seasons would
    compare a team's August with its May of another year.
    """

    team: Team
    season: str
    standing: StandingRow | None
    form: FormSummary
    splits: TeamSplits
    upcoming: list[UpcomingMatch]
    outlook: SimulationOutlook | None
