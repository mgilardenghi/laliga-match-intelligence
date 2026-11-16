"""The three rules of the relegation race, computed only from the data the caller hands in.

:func:`survival_threshold`, :func:`nearest_rivals` and :func:`next_match_swing`
are pure. The rival selection reads a club's run of the season simulation; the
threshold reads one stored season per row, with its size, its result count and
its final points by position; the swing reads the stored outcome counts of one
club's next match in that run. The route
``GET /v1/teams/{team_id}/relegation-race`` produces the season rows with
:func:`lmi_core.standings.compute_standings` -- the only ranking in the
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

:func:`next_match_swing` answers "what does each result of the next match do to
this club". The simulator stores one row per result of the club's next match, each
saying how often that result came out of the run and, among those samples, how
often the club was relegated, reached Europe, won the title, and how many points
it took. The function returns one :class:`OutcomeSwing` per result, in win, draw
and loss order:

- ``p_outcome`` is ``n_outcome / n_runs``, the share of the simulated runs that
  produced this result. ``p_relegation``, ``p_europe`` and ``p_title`` are
  ``n_relegated / n_outcome`` and its two siblings: what followed *this result*,
  not what the season holds. ``expected_points`` is ``points_sum / n_outcome``,
  the mean total the club reached after it.
- ``p_outcome`` is the sampled frequency, not a model probability. So
  ``sum_o p_outcome * p_relegation_o`` must equal the club's own stored
  ``p_relegation`` up to float rounding, and the same for Europe and the title.
  That identity is what lets the payload show the swing beside the club's
  probabilities without the two contradicting each other; a smoothed or
  renormalised ``p_outcome`` would break it.
- No rows, an outcome set that is not exactly one win, one draw and one loss
  (including a repeated result), a stored ``match_id`` that differs from the one
  the caller asks for, and any ``n_outcome`` of zero all return ``None``. The
  freshness case is a fact rather than a fault: on the as-of day a club whose
  next match was already played has stored rows for a match that is no longer
  next, and the answer stays unknown until the next run replaces them. A result
  the run never sampled has no conditional probability, and dividing by its zero
  count would invent one, so it is served as unknown too.
- It raises :class:`ValueError` when the rows name more than one ``match_id`` (a
  club has one next match), when a conditional count exceeds its ``n_outcome``
  (a run cannot relegate a club in more samples than produced the result), when
  the three ``n_outcome`` do not sum to ``n_runs`` (the rows are not one complete
  run), and when ``n_runs`` is below 1 (the frequency would be undefined, and the
  database forbids the value). A broken stored invariant is a bug: it is raised,
  never served as ``None``.
- The checks run in one fixed order -- no rows, then ``n_runs`` below 1, then the
  single ``match_id``, then the conditional counts, then the three outcomes, then
  freshness, then a never-sampled outcome, then the sum, then the three swings --
  so the same rows always give the same answer or the same error. The
  never-sampled outcome is decided before the sum is checked, because a run that
  could not sample a result is a legitimate run, while rows that do not add up
  are not.

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

OUTCOMES: Final = ("win", "draw", "loss")
"""The results the next match can have, in the order the swing reports them."""


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


@dataclass(frozen=True, slots=True)
class OutcomeCounts:
    """One stored result of a club's next match: how often it happened and what followed.

    ``outcome`` is ``"win"``, ``"draw"`` or ``"loss"`` and ``match_id`` the match
    the row describes, which is what the freshness guard compares.
    ``n_outcome`` counts the simulated runs that produced the result, and the
    three other counts say how many of *those* runs ended in relegation, in
    Europe and in the title. ``points_sum`` is the total points those runs earned,
    so the mean is that sum over ``n_outcome``.
    """

    match_id: int
    outcome: str
    n_outcome: int
    n_relegated: int
    n_europe: int
    n_title: int
    points_sum: int


@dataclass(frozen=True, slots=True)
class OutcomeSwing:
    """What one result of the next match does to the club, as the stored run measured it.

    ``p_outcome`` is the share of the simulated runs that produced this result,
    while ``p_relegation``, ``p_europe`` and ``p_title`` are the shares of those
    same runs that ended each way: what follows the result, not the season.
    ``expected_points`` is the mean total the club reached after it. ``outcome``
    names the result so the caller can put each swing in its own field.
    """

    outcome: str
    p_outcome: float
    p_relegation: float
    p_europe: float
    p_title: float
    expected_points: float


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


def next_match_swing(
    rows: Sequence[OutcomeCounts], *, n_runs: int, match_id: int
) -> tuple[OutcomeSwing, OutcomeSwing, OutcomeSwing] | None:
    """Return one swing per result of the club's next match, or ``None`` when unknown.

    ``rows`` is the stored outcome detail of one club in one simulation run:
    exactly one row per result. ``n_runs`` is that run's sample size and
    ``match_id`` the next match the caller believes the club plays, so a sample
    for a match already played is answered with ``None`` instead of with a swing
    nobody can act on. The three results come back in win, draw and loss order,
    each carrying its name. Rows that contradict their own counts raise
    :class:`ValueError`; nothing is invented here, neither a probability nor a
    null.
    """
    if not rows:
        return None
    if n_runs < 1:
        raise ValueError(f"n_runs must be at least 1, got {n_runs}")
    stored_match_id = _single_match_id(rows)
    for row in rows:
        _check_conditional("n_relegated", row.n_relegated, row.n_outcome)
        _check_conditional("n_europe", row.n_europe, row.n_outcome)
        _check_conditional("n_title", row.n_title, row.n_outcome)
    by_outcome = _rows_by_outcome(rows)
    if by_outcome is None:
        return None
    if stored_match_id != match_id:
        return None
    if any(row.n_outcome == 0 for row in by_outcome.values()):
        return None
    if sum(row.n_outcome for row in by_outcome.values()) != n_runs:
        raise ValueError(
            f"the outcomes of match {stored_match_id} do not sum to {n_runs} runs: "
            + ", ".join(f"{row.outcome} {row.n_outcome}" for row in rows)
        )
    return (
        _swing(by_outcome["win"], n_runs),
        _swing(by_outcome["draw"], n_runs),
        _swing(by_outcome["loss"], n_runs),
    )


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


def _check_conditional(name: str, count: int, n_outcome: int) -> None:
    """Raise when a conditional count is larger than the samples that produced the result.

    ``n_relegated``, ``n_europe`` and ``n_title`` are counts within one outcome's
    samples, so any of them above ``n_outcome`` makes the conditional probability
    exceed one. That row cannot have come from one complete run, and serving a
    probability above one, or the ``None`` that hides it, would hide the bug.
    """
    if count > n_outcome:
        raise ValueError(f"{name} is {count}, above its {n_outcome} samples")


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


def _rows_by_outcome(rows: Sequence[OutcomeCounts]) -> dict[str, OutcomeCounts] | None:
    """Return the rows keyed by result, or ``None`` when they are not one row per result.

    The derivation needs exactly one ``win``, one ``draw`` and one ``loss``. A
    missing result leaves a swing no caller can name, and a repeated one leaves
    two rows claiming the same result, so neither is split into three swings: the
    answer is unknown until the run stores a complete set.
    """
    by_outcome: dict[str, OutcomeCounts] = {}
    for row in rows:
        if row.outcome in by_outcome:
            return None
        by_outcome[row.outcome] = row
    if set(by_outcome) != set(OUTCOMES):
        return None
    return by_outcome


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


def _single_match_id(rows: Sequence[OutcomeCounts]) -> int:
    """Return the one match the rows describe, rejecting a set that names several.

    Rows of two matches cannot be one club's next match: the stages of a run are
    stored per match, and adding them together would report the swings of the
    wrong fixture. The distinct ``match_id`` values are the whole check, because
    the count of rows is decided later, by the outcome set and the sum.
    """
    match_ids = {row.match_id for row in rows}
    if len(match_ids) > 1:
        raise ValueError(f"the rows name more than one match: {sorted(match_ids)}")
    return match_ids.pop()


def _swing(row: OutcomeCounts, n_runs: int) -> OutcomeSwing:
    """Return one result's swing, dividing the stored counts by their samples.

    ``n_outcome`` is nonzero and no smaller than every conditional count when the
    caller reaches here, so no division is undefined and no probability exceeds one. The
    frequency uses ``n_runs`` and the consequences use ``n_outcome``, which is
    the difference between "how often this result happens" and "what happens when
    it does".
    """
    return OutcomeSwing(
        outcome=row.outcome,
        p_outcome=row.n_outcome / n_runs,
        p_relegation=row.n_relegated / row.n_outcome,
        p_europe=row.n_europe / row.n_outcome,
        p_title=row.n_title / row.n_outcome,
        expected_points=row.points_sum / row.n_outcome,
    )


def _table_order(team: RankedTeam) -> tuple[float, int]:
    """Return the serving order of one club: expected position, then identifier.

    The position decides because the race is read in table order, and the
    identifier ends the order so two clubs projected into the same place still
    arrive in one fixed order.
    """
    return (team.expected_position, team.team_id)
