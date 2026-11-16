"""Season statistics for one team, computed only from the data the caller hands in.

:func:`summarise` is pure. The caller -- the season-stats route -- loads the
team's played matches, the league's per-side lines and, when it needs them, the
statistics rows, and this module turns them into averages, counters and a
chronological match list. Nothing here reads the clock, the database or a file,
so the same arguments always produce the same :class:`SeasonStats`.

NULL IS NEVER ZERO. Every optional field means "not known", exactly as the
database schema and the API schemas state. A statistic that no counted match
carries is ``None``, not ``0.0``: a team with no expected-goals rows has an
unknown xG average, while a team whose rows all say zero has an average of
``0.0``. An average over an empty set does not exist either, which is why
``points_per_game`` and every field of the first match's :class:`RollingForm`
are ``None`` rather than zero. The same rule governs coverage counters: they
count the matches that carry the data, so the client can tell an average of one
match from an average of six.

The rules:

- :func:`match_points` is three for a win, one for a draw and none for a loss.
- :func:`average_line` averages ``goals`` over every line it is given. Every
  other field is averaged over only the lines that carry it -- a line with no
  :class:`StatValues`, or with that field ``None``, is left out of that mean and
  of no other. An empty input gives an all-``None`` line.
- :func:`match_record` counts wins, draws, losses, goals and points, and adds
  ``points_per_game``, which is ``None`` over no matches.
- :func:`summarise` orders the matches by ``(kickoff_date, match_id)``
  ascending, so a team that played twice on one date has a stable order that
  does not depend on the caller's list order; the caller's list is not sorted
  in place. ``xpts_market`` sums only the matches that carry an expected-points
  value and is ``None`` when none do, and ``xpts_coverage`` says how many did.
  ``stat_coverage`` counts the matches with a statistics row and ``xg_coverage``
  the matches with an expected-goals value on either side. Each match row's
  ``rolling5`` covers the at most five matches immediately before it in that
  order, never the match itself, and every rolling field is ``None`` for the
  team's first match.
"""

import datetime
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

Venue = Literal["home", "away"]
"""Where the team played a match: ``"home"`` or ``"away"``."""

_WIN_POINTS = 3
_DRAW_POINTS = 1
_ROLLING_WINDOW = 5


@dataclass(frozen=True, slots=True)
class StatValues:
    """One side's per-match statistics, every field optional.

    ``None`` means the source did not record that number for this match, so it
    must stay out of the corresponding average instead of counting as zero. The
    whole object is absent when the match has no statistics row at all.
    """

    shots: int | None = None
    shots_on_target: int | None = None
    fouls: int | None = None
    corners: int | None = None
    yellow: int | None = None
    red: int | None = None
    xg: float | None = None


@dataclass(frozen=True, slots=True)
class SideLine:
    """One side of one match, as an average sees it.

    ``goals`` is always known for a played match, so it is not optional and it
    enters the goals average of every line. ``stats`` is ``None`` when the match
    has no statistics row; a line that carries only goals is still a valid input
    to :func:`average_line`.
    """

    goals: int
    stats: StatValues | None


@dataclass(frozen=True, slots=True)
class StatLine:
    """A per-match average line; every field is ``None`` when it has no input.

    Used for the team's own numbers, the opponents' numbers and the league
    average, so the three are compared field by field. A ``None`` field says the
    counted matches carry no value for it, which a client renders as unknown.
    """

    goals: float | None
    shots: float | None
    shots_on_target: float | None
    fouls: float | None
    corners: float | None
    yellow: float | None
    red: float | None
    xg: float | None


@dataclass(frozen=True, slots=True)
class PlayedMatch:
    """One match a team played, already seen from that team's side.

    The caller flips the stored home/away score into ``goals_for`` and
    ``goals_against`` and passes the two statistics rows as ``stats_for`` and
    ``stats_against``. ``xpts`` is the expected points of this match the caller
    derived from the pre-closing market odds, ``None`` when the fixture carries
    no eligible price: per-match predictions are not stored, so no stored
    forecast is an input here. A scheduled match is not a :class:`PlayedMatch`
    and never reaches this module.
    """

    match_id: int
    kickoff_date: datetime.date
    venue: Venue
    opponent_id: int
    goals_for: int
    goals_against: int
    xpts: float | None
    stats_for: StatValues | None
    stats_against: StatValues | None


@dataclass(frozen=True, slots=True)
class MatchRecord:
    """Win/draw/loss counters over a group of matches.

    ``points_per_game`` is ``None`` when ``played`` is zero, because an average
    over no match does not exist. It is derived rather than stored per match so
    a caller cannot keep the counters and the average out of step.
    """

    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    points: int
    points_per_game: float | None


@dataclass(frozen=True, slots=True)
class RollingForm:
    """Averages over the matches immediately before one match.

    The window never includes the match it describes: a form line answers what
    the team did before kickoff, which is what a reader of a season table wants
    and what keeps the row usable as a pre-match feature. Every field is
    ``None`` when the window is empty.
    """

    points_pg: float | None
    xpts_pg: float | None
    goals_for_pg: float | None
    goals_against_pg: float | None


@dataclass(frozen=True, slots=True)
class MatchLine:
    """One row of a team's season.

    ``shots_for`` and ``shots_against`` are the shots columns, not a full
    statistics line, because the table shows only shots. They are ``None`` when
    that side of the match has no statistics row or no shots value.
    """

    match_id: int
    venue: Venue
    points: int
    xpts: float | None
    goals_for: int
    goals_against: int
    shots_for: int | None
    shots_against: int | None
    rolling5: RollingForm


@dataclass(frozen=True, slots=True)
class SeasonStats:
    """A team's whole season as averages, counters and one row per match.

    ``xpts_market`` is a sum, not an average, because expected points are
    additive over matches; it is ``None`` when no match carries one, and
    ``xpts_coverage`` tells the client how many matches the sum rests on. The
    coverage counters exist for the same reason on the statistics side: an
    average is only as trustworthy as the number of matches behind it.
    """

    played: int
    points: int
    xpts_market: float | None
    xpts_coverage: int
    per_match_for: StatLine
    per_match_against: StatLine
    league_avg: StatLine
    stat_coverage: int
    xg_coverage: int
    matches: tuple[MatchLine, ...]


def match_points(goals_for: int, goals_against: int) -> int:
    """Return the league points a team earns from one score: 3 win, 1 draw, 0 loss."""
    if goals_for > goals_against:
        return _WIN_POINTS
    if goals_for == goals_against:
        return _DRAW_POINTS
    return 0


def average_line(lines: Iterable[SideLine]) -> StatLine:
    """Return the per-match means of ``lines``, ignoring missing values instead of zeroing them.

    ``goals`` is averaged over every line. Each statistic is averaged over only
    the lines that carry it, so the denominators can differ by field; a field no
    line carries stays ``None``, and an empty input gives an all-``None`` line.
    The input is consumed once, so a generator is accepted.
    """
    rows = list(lines)
    stats = [line.stats for line in rows if line.stats is not None]
    return StatLine(
        goals=_mean(line.goals for line in rows),
        shots=_mean(value.shots for value in stats if value.shots is not None),
        shots_on_target=_mean(
            value.shots_on_target for value in stats if value.shots_on_target is not None
        ),
        fouls=_mean(value.fouls for value in stats if value.fouls is not None),
        corners=_mean(value.corners for value in stats if value.corners is not None),
        yellow=_mean(value.yellow for value in stats if value.yellow is not None),
        red=_mean(value.red for value in stats if value.red is not None),
        xg=_mean(value.xg for value in stats if value.xg is not None),
    )


def match_record(matches: Iterable[PlayedMatch]) -> MatchRecord:
    """Return the win/draw/loss counters over ``matches``, each seen from its team's side.

    ``points_per_game`` is derived from the counters and is ``None`` when no
    match was counted, so an empty input is an all-zero record without a made-up
    average. The input is consumed once.
    """
    played = won = drawn = lost = goals_for = goals_against = points = 0
    for match in matches:
        played += 1
        goals_for += match.goals_for
        goals_against += match.goals_against
        earned = match_points(match.goals_for, match.goals_against)
        points += earned
        if earned == _WIN_POINTS:
            won += 1
        elif earned == _DRAW_POINTS:
            drawn += 1
        else:
            lost += 1
    return MatchRecord(
        played=played,
        won=won,
        drawn=drawn,
        lost=lost,
        goals_for=goals_for,
        goals_against=goals_against,
        points=points,
        points_per_game=points / played if played else None,
    )


def summarise(matches: Sequence[PlayedMatch], *, league: Iterable[SideLine]) -> SeasonStats:
    """Return the season of one team from its played matches and the league's lines.

    ``matches`` is the team's season as the caller loaded it, in any order; this
    function orders a copy by ``(kickoff_date, match_id)`` and leaves the
    argument untouched. ``league`` holds the league-wide per-side lines the
    caller wants as the comparison baseline -- which matches those cover is the
    caller's decision -- and is consumed once. Nothing here invents a value:
    every statistic, expected-points total and rolling average follows the
    null-is-never-zero rule in the module docstring.
    """
    ordered = sorted(matches, key=_chronological)
    record = match_record(ordered)
    xpts_values = [match.xpts for match in ordered if match.xpts is not None]
    rows = tuple(
        MatchLine(
            match_id=match.match_id,
            venue=match.venue,
            points=match_points(match.goals_for, match.goals_against),
            xpts=match.xpts,
            goals_for=match.goals_for,
            goals_against=match.goals_against,
            shots_for=_shots(match.stats_for),
            shots_against=_shots(match.stats_against),
            rolling5=_rolling(ordered[max(0, index - _ROLLING_WINDOW) : index]),
        )
        for index, match in enumerate(ordered)
    )
    return SeasonStats(
        played=record.played,
        points=record.points,
        xpts_market=sum(xpts_values) if xpts_values else None,
        xpts_coverage=len(xpts_values),
        per_match_for=average_line(
            SideLine(goals=match.goals_for, stats=match.stats_for) for match in ordered
        ),
        per_match_against=average_line(
            SideLine(goals=match.goals_against, stats=match.stats_against) for match in ordered
        ),
        league_avg=average_line(league),
        stat_coverage=sum(1 for match in ordered if match.stats_for is not None),
        xg_coverage=sum(1 for match in ordered if _carries_xg(match)),
        matches=rows,
    )


def _chronological(match: PlayedMatch) -> tuple[datetime.date, int]:
    """Return the sort key that breaks a same-date tie by match id, never by input order."""
    return (match.kickoff_date, match.match_id)


def _mean(values: Iterable[float]) -> float | None:
    """Return the arithmetic mean of ``values``, or ``None`` when there is nothing to average."""
    numbers = list(values)
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _shots(stats: StatValues | None) -> int | None:
    """Return the shots of one side, or ``None`` when the side has no statistics row."""
    return None if stats is None else stats.shots


def _carries_xg(match: PlayedMatch) -> bool:
    """Return whether either side of ``match`` carries an expected-goals value."""
    return any(
        stats is not None and stats.xg is not None
        for stats in (match.stats_for, match.stats_against)
    )


def _rolling(window: Sequence[PlayedMatch]) -> RollingForm:
    """Return the means over ``window``; every field is ``None`` when the window is empty."""
    if not window:
        return RollingForm(
            points_pg=None,
            xpts_pg=None,
            goals_for_pg=None,
            goals_against_pg=None,
        )
    return RollingForm(
        points_pg=_mean(match_points(match.goals_for, match.goals_against) for match in window),
        xpts_pg=_mean(match.xpts for match in window if match.xpts is not None),
        goals_for_pg=_mean(match.goals_for for match in window),
        goals_against_pg=_mean(match.goals_against for match in window),
    )
