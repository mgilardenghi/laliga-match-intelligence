"""One club's multi-season history, computed only from the data the caller hands in.

:func:`summarise` is pure. The caller -- the history route -- loads one club's
played matches grouped by season and, with each group, the club's row in that
season's table; this module turns them into the per-season line the payload
serves, plus one entry per metric naming the steepest drop between two adjacent
seasons. Nothing here reads the clock, the database or a file, so the same
arguments always produce the same :class:`TeamHistory`.

``sot`` abbreviates shots on target. The payload field names use the
abbreviation, so the dataclasses follow the payload rather than inventing a
second spelling for one number.

NULL IS NEVER ZERO. Every optional field means "not known", exactly as the
database schema and the API schemas state. A statistic that no counted match
carries is ``None``, not ``0.0``: a club with no expected-points rows has an
unknown expected-points average, while a club whose rows all record zero has an
average of ``0.0``. The same rule governs the drop: a pair with a missing value
on either side is skipped for that metric instead of being read as a fall to
zero, because a gap in the source is not a decline on the pitch.

The rules:

- :func:`summarise` consumes ``seasons`` once and never sorts the argument in
  place. The output is ascending by ``(start_year, season)``: the start year
  orders the seasons and the code breaks a tie, so a caller that lists two
  seasons of one start year still receives one fixed order, and a reader never
  has to trust the order the caller happened to load.
- A :class:`SeasonInput` with no match produces no :class:`SeasonLine`. The
  payload lists only the seasons the club played, so a season it did not play
  is absent rather than a line of zeros, and an empty input gives an empty
  history.
- ``played`` is the match count, and ``points`` is the sum of
  :func:`lmi_core.analytics.season_stats.match_points` over those matches, so
  the points of a result are defined in one place only. ``points_pg``,
  ``goals_for_pg`` and ``goals_against_pg`` divide by ``played`` and are
  therefore never ``None`` for a listed season.
- Every other average is the mean over only the matches that carry the value,
  so the denominators can differ from ``played`` and from each other; a field no
  match carries is ``None``. ``cards_pg`` is the mean of ``cards_for``, the
  club's own card count per match, which the caller sums from the yellow and red
  columns. It is neither ``cards_for - cards_against`` nor a mean over both
  sides, because only the club's own cards describe the club.
- ``position`` is passed through unchanged, ``None`` included: a club with no
  table row for a season has an unknown position, and the payload must say so
  instead of showing a rank nobody computed.
- ``largest_drop`` covers exactly the nine metrics the payload has a field for,
  each named with that field so a client can match an entry to the number it
  describes: ``points_pg``, ``goals_for_pg``, ``goals_against_pg``,
  ``shots_for_pg``, ``shots_against_pg``, ``sot_for_pg``, ``sot_against_pg``,
  ``cards_pg`` and ``xpts_pg``. Absolute ``points`` is deliberately not a
  metric: a season can be shorter, and a drop in totals would then describe the
  calendar instead of the club.
- A drop is measured between adjacent entries of the ascending output, with the
  earlier season as ``from_season`` and the later as ``to_season``, and only
  when both values are known; a pair with a ``None`` on either side is skipped
  for that metric and for no other.
- ``delta`` is oriented so a negative value always means the club got worse. For
  the higher-is-better metrics (``points_pg``, ``goals_for_pg``,
  ``shots_for_pg``, ``sot_for_pg``, ``xpts_pg``) it is ``later - earlier``; for
  the lower-is-better ones (``goals_against_pg``, ``shots_against_pg``,
  ``cards_pg``) it is ``earlier - later``, so conceding more and collecting more
  cards read as a fall like any other number that got worse.
- ``largest_drop`` holds at most one entry per metric: the most negative delta
  the club has between adjacent seasons. A metric that never declined is
  omitted, so the list is empty when nothing declined and when fewer than two
  seasons are listed. When two pairs decline by the same amount the earlier pair
  is kept, so the answer never depends on how the pairs were visited, and the
  list is sorted by ``delta`` ascending and then by ``metric`` ascending, so two
  reads of one history serve the same order.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from lmi_core.analytics.season_stats import match_points


@dataclass(frozen=True, slots=True)
class SeasonMatch:
    """One played match of one season, seen from the club's side.

    The caller flips the stored home/away score into ``goals_for`` and
    ``goals_against``, sums the club's yellow and red columns into ``cards_for``
    and the opponent's into ``cards_against``, and leaves a statistic ``None``
    when the match has no statistics row or that row has no value for it. A
    scheduled match is not a :class:`SeasonMatch` and never reaches this module.

    ``cards_against`` completes the view of a match but does not enter
    :class:`SeasonLine`: the payload reports only the club's own cards per game,
    so a mean over both sides would describe neither.
    """

    goals_for: int
    goals_against: int
    xpts: float | None
    shots_for: int | None
    shots_against: int | None
    sot_for: int | None
    sot_against: int | None
    cards_for: int | None
    cards_against: int | None


@dataclass(frozen=True, slots=True)
class SeasonInput:
    """One season the club played, with its table row and the matches it played.

    ``season`` is the season code the payload shows and ``start_year`` the year
    it began, which together order the history. ``position`` is the club's rank
    in that season's table, ``None`` when the club has no table row; it is copied
    into the line unchanged. ``matches`` may be empty, in which case the season
    is not listed at all.
    """

    season: str
    start_year: int
    position: int | None
    matches: tuple[SeasonMatch, ...]


@dataclass(frozen=True, slots=True)
class SeasonLine:
    """One season of a club's history, as the payload serves it.

    The field names are the payload's names, with ``pg`` for per game and ``sot``
    for shots on target. ``played``, ``points``, ``points_pg``, ``goals_for_pg``
    and ``goals_against_pg`` are always known for a listed season; every other
    average is ``None`` when no match of that season carries the value, and is
    averaged over only the matches that do.
    """

    season: str
    position: int | None
    played: int
    points: int
    points_pg: float
    goals_for_pg: float
    goals_against_pg: float
    shots_for_pg: float | None
    shots_against_pg: float | None
    sot_for_pg: float | None
    sot_against_pg: float | None
    cards_pg: float | None
    xpts_pg: float | None


@dataclass(frozen=True, slots=True)
class MetricDrop:
    """The steepest decline of one metric between two adjacent seasons.

    ``delta`` is negative and oriented so it always means the club got worse;
    ``from_season`` is the earlier season code and ``to_season`` the later, both
    taken from the ascending history. ``metric`` is the payload key of the number
    the entry describes, so a client can find the two values behind the delta.
    """

    metric: str
    from_season: str
    to_season: str
    delta: float


@dataclass(frozen=True, slots=True)
class TeamHistory:
    """A club's seasons, ascending, and the steepest decline of each metric.

    ``largest_drop`` is empty when nothing declined and when fewer than two
    seasons are listed, so a client renders no decline note rather than a zero.
    """

    seasons: tuple[SeasonLine, ...]
    largest_drop: tuple[MetricDrop, ...]


MetricReader = Callable[[SeasonLine], float | None]
"""Reads one drop metric from a line; the metric's name is the payload key."""

Direction = Literal["higher", "lower"]
"""Whether more of a metric is better for the club or worse."""

_METRICS: tuple[tuple[str, MetricReader, Direction], ...] = (
    ("points_pg", lambda line: line.points_pg, "higher"),
    ("goals_for_pg", lambda line: line.goals_for_pg, "higher"),
    ("goals_against_pg", lambda line: line.goals_against_pg, "lower"),
    ("shots_for_pg", lambda line: line.shots_for_pg, "higher"),
    ("shots_against_pg", lambda line: line.shots_against_pg, "lower"),
    ("sot_for_pg", lambda line: line.sot_for_pg, "higher"),
    ("sot_against_pg", lambda line: line.sot_against_pg, "lower"),
    ("cards_pg", lambda line: line.cards_pg, "lower"),
    ("xpts_pg", lambda line: line.xpts_pg, "higher"),
)
"""The drop metrics, in payload order, each with its reader and its direction.

The three lower-is-better metrics are the ones the club wants less of, so their
delta must be oriented the other way or an increase in goals conceded would read
as an improvement. The table is the only place a metric is named, so a reader of
the payload and a reader of this module cannot disagree about the list.
"""


def summarise(seasons: Iterable[SeasonInput]) -> TeamHistory:
    """Return one club's history from its seasons, in any order.

    ``seasons`` is consumed once and ordered on a copy by
    :func:`_season_order`, so the argument is left untouched and a generator is
    accepted. A season without a played match is dropped before the lines are
    built, which is also why the per-game figures of a listed season never
    divide by zero. The drops are then measured on those ascending lines only,
    so a skipped season neither ends a streak nor creates a pair.
    """
    ordered = sorted(seasons, key=_season_order)
    lines = tuple(_season_line(season) for season in ordered if season.matches)
    return TeamHistory(seasons=lines, largest_drop=_largest_drop(lines))


def _season_order(season: SeasonInput) -> tuple[int, str]:
    """Return the sort key of one season: start year first, then the season code.

    Two seasons of one start year are ordered by their code, so the order is
    total and does not fall back on the caller's list order.
    """
    return (season.start_year, season.season)


def _season_line(season: SeasonInput) -> SeasonLine:
    """Return the payload line of one season that has at least one match.

    ``played`` is the match count, so the three always-known averages divide by
    a positive number. Every optional average goes through :func:`_mean` over
    only the matches that carry the value, which is how the denominators come to
    differ by field.
    """
    matches = season.matches
    played = len(matches)
    points = sum(match_points(match.goals_for, match.goals_against) for match in matches)
    return SeasonLine(
        season=season.season,
        position=season.position,
        played=played,
        points=points,
        points_pg=points / played,
        goals_for_pg=sum(match.goals_for for match in matches) / played,
        goals_against_pg=sum(match.goals_against for match in matches) / played,
        shots_for_pg=_mean(match.shots_for for match in matches if match.shots_for is not None),
        shots_against_pg=_mean(
            match.shots_against for match in matches if match.shots_against is not None
        ),
        sot_for_pg=_mean(match.sot_for for match in matches if match.sot_for is not None),
        sot_against_pg=_mean(
            match.sot_against for match in matches if match.sot_against is not None
        ),
        cards_pg=_mean(match.cards_for for match in matches if match.cards_for is not None),
        xpts_pg=_mean(match.xpts for match in matches if match.xpts is not None),
    )


def _largest_drop(lines: Sequence[SeasonLine]) -> tuple[MetricDrop, ...]:
    """Return the steepest decline of each metric, sorted by delta then metric.

    Only adjacent pairs of the ascending ``lines`` are compared, so the delta
    always spans consecutive seasons of the club rather than two seasons picked
    to flatter the story. A pair with a ``None`` on either side is skipped for
    that metric alone. A metric is kept only when its delta is negative, and a
    later pair replaces the kept entry only when it is strictly steeper, so a tie
    keeps the earlier pair and the result never depends on the iteration order.
    """
    steepest: dict[str, MetricDrop] = {}
    for earlier, later in pairwise(lines):
        for metric, read, direction in _METRICS:
            before = read(earlier)
            after = read(later)
            if before is None or after is None:
                continue
            delta = _oriented_delta(earlier=before, later=after, better_when=direction)
            kept = steepest.get(metric)
            if delta < 0 and (kept is None or delta < kept.delta):
                steepest[metric] = MetricDrop(
                    metric=metric,
                    from_season=earlier.season,
                    to_season=later.season,
                    delta=delta,
                )
    return tuple(sorted(steepest.values(), key=_drop_order))


def _oriented_delta(*, earlier: float, later: float, better_when: Direction) -> float:
    """Return the change from ``earlier`` to ``later``, negative when the club got worse.

    A higher-is-better metric falls when the later value is smaller, and a
    lower-is-better metric falls when it is larger, so the subtraction is flipped
    for the second kind. One sign convention for all nine metrics is what lets a
    client read a negative delta without knowing which column it came from.
    """
    return later - earlier if better_when == "higher" else earlier - later


def _drop_order(drop: MetricDrop) -> tuple[float, str]:
    """Return the sort key of a drop: delta ascending, then metric name ascending.

    The delta decides first because the list answers "what fell most", and the
    metric name breaks a tie so two reads of one history serve the same order
    whatever order the dict happened to hold.
    """
    return (drop.delta, drop.metric)


def _mean(values: Iterable[float]) -> float | None:
    """Return the arithmetic mean of ``values``, or ``None`` when there is nothing to average."""
    numbers = list(values)
    if not numbers:
        return None
    return sum(numbers) / len(numbers)
