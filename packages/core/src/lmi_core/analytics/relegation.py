"""The two rules of the relegation race, computed only from the data the caller hands in.

:func:`survival_threshold` and :func:`nearest_rivals` are pure. The rival
selection reads a club's run of the season simulation; the threshold reads one
stored season per row, with its size, its result count and its final points by
position. The route ``GET /v1/teams/{team_id}/relegation-race`` produces those
rows with :func:`lmi_core.standings.compute_standings` -- the only ranking in the
repository -- and this module never ranks anything itself. Nothing here reads the
clock, the database or a file, and nothing here restates a configuration number,
so the same arguments always produce the same answer.

:func:`survival_threshold` answers "how many points usually keep a club up". It
reads one number per season, the final total of the highest relegated place, and
reports the sample's median and extremes:

- A season is counted only when all four hold: ``n_teams`` is :data:`LEAGUE_SIZE`,
  ``start_year`` is at least :data:`FIRST_START_YEAR`, ``len(points)`` is
  ``n_teams``, and ``results_counted`` is ``n_teams * (n_teams - 1)``. The last
  is what "completed" means here: a double round robin of twenty clubs is 380
  results, so a season whose fixtures are not all played is a partial table and
  its seventeenth place is not a final total. Counting it would put a number the
  calendar produced into a sample of season totals.
- The counted position is ``n_teams - relegation_positions``, so the configured
  three relegation places of twenty clubs make it 17. The caller passes that
  count in, because ``lmi_core.simulation.engine.Qualification`` owns it and a
  second copy here would drift from it. The number used is reported back as
  ``position``, so the payload can name the place it measured.
- The sample holds one total per counted season, never the totals of several
  places: several places would describe how a table is spread rather than how
  many points a club needs to survive it.
- ``seasons_used`` is the sample size, ``points_min`` and ``points_max`` its
  extremes, and ``points_median`` its median -- the middle value for an odd count
  and the mean of the two middle values for an even one -- so the median is a
  ``float`` and is never rounded. A whole number that the sample does not contain
  would be an invented total.
- No counted season means no threshold: the function returns ``None``. It does
  not fall back to the current table and it does not serve zero, because a
  threshold borrowed from an in-progress season, or a zero that no sample
  produced, would read to a client as a survival line somebody computed.

It raises :class:`ValueError` when ``relegation_positions`` is below 1 or is not
smaller than :data:`LEAGUE_SIZE` (the counted position would fall outside the
table), and when a :class:`SeasonTable` carries more ``points`` than ``n_teams``
-- a table that contradicts its own size. Every table is checked, whether or not
its season would have been counted, so a malformed row cannot hide behind the
filter. A *shorter* ``points`` tuple is not an error: a season with a missing
table row looks like that, and the rule above simply leaves the season out.

:func:`nearest_rivals` answers "which clubs is this one racing". It returns the
subject club plus the ``count`` clubs nearest to it by expected final position,
ordered by ``expected_position`` ascending and then by ``team_id`` ascending, so
the caller can render the group in table order:

- Distance is ``abs(expected_position - subject.expected_position)``, not the raw
  position difference: the subject is included by construction, its neighbours
  may sit above or below it, and only the absolute distance treats the two sides
  as one neighbourhood.
- A tie in distance is broken by the smaller ``expected_position`` and then by
  the smaller ``team_id``, so the choice never depends on the order the rows were
  read in.
- Fewer than ``count`` other clubs means the result is the subject plus every
  other club. It is never padded.

It raises :class:`ValueError` when ``team_id`` is not among ``teams`` (the caller
checks membership and decides what to serve), when two rows share a ``team_id``
(the race cannot say which of them is the club), and when ``count`` is negative.

Two deviations from the obvious implementation, and why each is honest:

- Completeness is proved from the result count, never from the clock. This module
  does not know which season is current and must not ask: a stored season is a
  row, and whether it is the one being played is the caller's fact. A season with
  an unplayed fixture is therefore skipped rather than treated as final, so an
  in-progress table never enters the sample.
- The threshold is a historical sample, not a projection: it is the median of
  what the counted seasons actually needed, served with ``seasons_used``,
  ``points_min`` and ``points_max`` beside it, and ``None`` when the sample is
  empty. A reader can see how thin the evidence is, and an unknown threshold is
  shown as unknown instead of as a number nobody computed.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

LEAGUE_SIZE: Final = 20
"""The league size the threshold counts, which the reference seasons all have."""

FIRST_START_YEAR: Final = 2005
"""The first season the threshold counts: 2005-06, the start of the stored history."""


@dataclass(frozen=True, slots=True)
class SeasonTable:
    """One stored season as the threshold sees it: its size and its final points by position.

    ``season`` is the season code the payload shows and ``start_year`` the year it
    began, which is what decides whether the threshold counts it. ``n_teams`` is
    the size of that season's league and ``results_counted`` how many of its
    fixtures were played, so a caller can prove a season complete without this
    module knowing a calendar. ``points`` holds the final total of each position,
    index 0 being position 1, so it is in table order.
    """

    season: str
    start_year: int
    n_teams: int
    results_counted: int
    points: tuple[int, ...]
    """Final points by position: ``points[position - 1]`` is that place's total."""


@dataclass(frozen=True, slots=True)
class SurvivalThreshold:
    """The historical survival line: the counted place's totals, summarised.

    ``position`` is the place the sample was read from, so the payload can say
    which place it measured and the number is not a mystery. ``seasons_used``
    makes the sample size visible, and the two extremes show how far the counted
    seasons ranged around the median.
    """

    points_median: float
    points_min: int
    points_max: int
    seasons_used: int
    position: int


@dataclass(frozen=True, slots=True)
class RankedTeam:
    """One club of the latest simulation run, with the columns the race compares.

    The fields are the simulation's own: the points the club has now, its expected
    final points and position, its relegation probability and the low and high
    ends of its projected total. The rival selection reads only ``team_id`` and
    ``expected_position``; the rest is served next to the chosen clubs.
    """

    team_id: int
    current_points: int
    expected_points: float
    expected_position: float
    p_relegation: float
    points_p05: int
    points_p95: int


def survival_threshold(
    tables: Iterable[SeasonTable], *, relegation_positions: int
) -> SurvivalThreshold | None:
    """Return the survival line of the counted seasons, or ``None`` when none counts.

    ``tables`` is consumed once. ``relegation_positions`` is the caller's
    configured count from ``Qualification``, and the counted place is the one just
    above those places. Each table is validated on the way past, and only the
    seasons the module docstring lists enter the sample, so one malformed or
    incomplete season can neither distort the median nor be silently read as
    final.
    """
    position = _counted_position(relegation_positions)
    sample: list[int] = []
    for table in tables:
        _check_points_fit(table)
        if _is_counted(table):
            sample.append(table.points[position - 1])
    if not sample:
        return None
    ordered = sorted(sample)
    return SurvivalThreshold(
        points_median=_median(ordered),
        points_min=ordered[0],
        points_max=ordered[-1],
        seasons_used=len(ordered),
        position=position,
    )


def nearest_rivals(teams: Sequence[RankedTeam], *, team_id: int, count: int = 4) -> tuple[int, ...]:
    """Return the subject club and the ``count`` clubs nearest to it, in table order.

    ``teams`` is the latest simulation run, one row per club. The subject is
    included, so a caller can render the whole group without looking it up again,
    and a field smaller than ``count + 1`` gives back every club. A duplicate
    identifier, an absent subject or a negative ``count`` raises
    :class:`ValueError`; the caller checks membership first and decides which
    answer to serve for a club the run does not hold.
    """
    if count < 0:
        raise ValueError(f"count must not be negative, got {count}")
    subject = _subject(teams, team_id)
    rivals = sorted(
        (team for team in teams if team.team_id != team_id),
        key=lambda team: (
            abs(team.expected_position - subject.expected_position),
            team.expected_position,
            team.team_id,
        ),
    )[:count]
    return tuple(team.team_id for team in sorted((subject, *rivals), key=_table_order))


def _counted_position(relegation_positions: int) -> int:
    """Return the place the sample is read from: the one just above the bottom places.

    The count is validated against :data:`LEAGUE_SIZE` because every counted
    season has that size, so a count that leaves the table would name a place no
    counted season has and the sample would be empty for the wrong reason.
    """
    if relegation_positions < 1:
        raise ValueError(f"relegation_positions must be at least 1, got {relegation_positions}")
    if relegation_positions >= LEAGUE_SIZE:
        raise ValueError(
            f"relegation_positions must be smaller than {LEAGUE_SIZE}, got {relegation_positions}"
        )
    return LEAGUE_SIZE - relegation_positions


def _check_points_fit(table: SeasonTable) -> None:
    """Raise when a season lists more point totals than it has clubs.

    A longer tuple means the caller built the row from something other than that
    season's table, and reading a place out of it would quietly use a total from
    another league. A shorter tuple is left to the counting rule instead, because
    a missing row and a malformed table are different problems.
    """
    if len(table.points) > table.n_teams:
        raise ValueError(
            f"season {table.season} has {len(table.points)} point totals for {table.n_teams} clubs"
        )


def _is_counted(table: SeasonTable) -> bool:
    """Return whether a season is a completed 20-club season of the stored history.

    All four conditions are needed. The size keeps one league's survival line from
    being read out of another's table, the start year keeps the sample inside the
    stored history, the tuple length guards the read itself, and the result count
    is the proof that every fixture was played, so a partial table never supplies
    a "final" total.
    """
    return (
        table.n_teams == LEAGUE_SIZE
        and table.start_year >= FIRST_START_YEAR
        and len(table.points) == table.n_teams
        and table.results_counted == table.n_teams * (table.n_teams - 1)
    )


def _median(ordered: Sequence[int]) -> float:
    """Return the median of an ascending non-empty sample, never rounded.

    An odd count has a middle value; an even count has two, and their mean is the
    median even when it lands half a point between them. Rounding it would serve a
    total that none of the counted seasons reached.
    """
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def _subject(teams: Sequence[RankedTeam], team_id: int) -> RankedTeam:
    """Return the row of ``team_id``, rejecting a duplicate identifier or an absent club.

    The duplicates are found on the same pass, because a repeated identifier makes
    every later comparison ambiguous: two rows claiming to be one club would
    decide both the subject and a neighbour by their position in the list.
    """
    seen: set[int] = set()
    subject: RankedTeam | None = None
    for team in teams:
        if team.team_id in seen:
            raise ValueError(f"team {team.team_id} appears more than once")
        seen.add(team.team_id)
        if team.team_id == team_id:
            subject = team
    if subject is None:
        raise ValueError(f"team {team_id} is not among the teams of the run")
    return subject


def _table_order(team: RankedTeam) -> tuple[float, int]:
    """Return the serving order of one club: expected position, then identifier.

    The position decides because the race is read in table order, and the
    identifier ends the order so two clubs projected into the same place still
    arrive in one fixed order.
    """
    return (team.expected_position, team.team_id)
