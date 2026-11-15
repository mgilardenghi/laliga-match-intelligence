"""League standings from played matches, with LaLiga's tie-break chain.

:func:`compute_standings` is pure. The caller hands in the season's team set and
the results it wants counted -- the API filters a season and an ``as_of`` date,
the M4 season simulation passes the matches it simulated -- and gets back one
row per team, so the same arguments always produce the same table. Nothing here
reads the clock, the database or a file.

Row order, applied top to bottom:

1. Points descending: three for a win, one for a draw, none for a loss. Point
   deductions are not modelled because the schema does not carry them.
2. Head-to-head, for a group of teams level on points, and only when the group
   is complete: every ordered pair inside it has been played. A two-team group
   compares head-to-head goal difference; a larger group compares mini-league
   points and then mini-league goal difference, both computed over the whole
   group.
3. Then, or when the group is incomplete, overall goal difference descending and
   overall goals scored descending.
4. Then the team name in Unicode code point order, and finally the team id.
   Positions are ``1..n`` with no shared place.

Two deviations from the official regulation:

- Fair play is not a tie-break. The competition uses it after goals scored and
  this table does not, so a tie that survives step 3 can be ordered differently
  from the official one.
- Head-to-head is not recursive. When a complete mini-league separates part of a
  group and leaves the rest level, the remaining teams are not compared again
  with fresh head-to-head totals; they fall through to step 3 like any other
  tie.

Away goals are never used, at any step.
"""

import datetime
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

_WIN_POINTS = 3
_DRAW_POINTS = 1
_FORM_LENGTH = 5


@dataclass(frozen=True, slots=True)
class MatchResult:
    """One played match, as the caller wants it counted against the table.

    ``kickoff_date`` is the match date, not an instant: it orders the form
    string and nothing else. A match that was never played has no
    ``MatchResult``, which is what makes an affected head-to-head group
    incomplete.
    """

    home_team_id: int
    away_team_id: int
    home_goals: int
    away_goals: int
    kickoff_date: datetime.date


@dataclass(frozen=True, slots=True)
class StandingRow:
    """One team's line in the table, with ``position`` its 1-based place."""

    position: int
    team_id: int
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int
    form: tuple[str, ...]
    """Last five outcomes, most recent first: ``"W"``, ``"D"`` or ``"L"``, at most five."""


@dataclass(slots=True)
class _Totals:
    """Running counts for one team, either over the season or over a mini-league."""

    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0

    @property
    def goal_difference(self) -> int:
        """Return ``goals_for - goals_against``."""
        return self.goals_for - self.goals_against


def compute_standings(
    teams: Mapping[int, str], results: Iterable[MatchResult]
) -> list[StandingRow]:
    """Return one row per team in ``teams``, ranked as the module docstring describes.

    ``teams`` maps every team id of the season to its name, and a team without a
    result keeps a row of zeros. ``results`` holds only the matches the caller
    wants counted, so an ``as_of`` cutoff or a simulated season is expressed by
    filtering that argument. A result that names a team missing from ``teams``,
    or that pairs a team with itself, raises :class:`ValueError`.
    """
    matches = list(results)
    totals = {team_id: _Totals() for team_id in teams}
    history: dict[int, list[tuple[datetime.date, str]]] = {team_id: [] for team_id in teams}
    for match in matches:
        if match.home_team_id == match.away_team_id:
            raise ValueError(f"team {match.home_team_id} cannot play itself")
        _add_match(
            _totals_of(totals, match.home_team_id),
            match.home_goals,
            match.away_goals,
        )
        _add_match(
            _totals_of(totals, match.away_team_id),
            match.away_goals,
            match.home_goals,
        )
        history[match.home_team_id].append(
            (match.kickoff_date, _outcome(match.home_goals, match.away_goals))
        )
        history[match.away_team_id].append(
            (match.kickoff_date, _outcome(match.away_goals, match.home_goals))
        )

    rows: list[StandingRow] = []
    for position, team_id in enumerate(_ranking(teams, totals, matches), start=1):
        total = totals[team_id]
        rows.append(
            StandingRow(
                position=position,
                team_id=team_id,
                played=total.played,
                won=total.won,
                drawn=total.drawn,
                lost=total.lost,
                goals_for=total.goals_for,
                goals_against=total.goals_against,
                goal_difference=total.goal_difference,
                points=total.points,
                form=_form(history[team_id]),
            )
        )
    return rows


def _ranking(
    teams: Mapping[int, str], totals: dict[int, _Totals], matches: list[MatchResult]
) -> list[int]:
    """Return the team ids in table order: point groups high to low, each group ranked."""
    by_points: dict[int, list[int]] = {}
    for team_id, total in totals.items():
        by_points.setdefault(total.points, []).append(team_id)
    ranking: list[int] = []
    for points in sorted(by_points, reverse=True):
        group = by_points[points]
        ranking.extend(_rank_group(group, totals, teams, matches))
    return ranking


def _rank_group(
    group: list[int],
    totals: dict[int, _Totals],
    teams: Mapping[int, str],
    matches: list[MatchResult],
) -> list[int]:
    """Order one group of teams level on points.

    Head-to-head enters the sort key only for a complete group; the rest of the
    key is what decides a sub-group that a mini-league left level, because no
    fresh head-to-head is computed for it.
    """
    head_to_head = _head_to_head(group, matches) if len(group) > 1 else None
    compare_inside = len(group) > 2

    def key(team_id: int) -> tuple[int, int, int, int, str, int]:
        total = totals[team_id]
        inside_points = 0
        inside_difference = 0
        if head_to_head is not None:
            inside = head_to_head[team_id]
            inside_difference = inside.goal_difference
            if compare_inside:
                inside_points = inside.points
        return (
            -inside_points,
            -inside_difference,
            -total.goal_difference,
            -total.goals_for,
            teams[team_id],
            team_id,
        )

    return sorted(group, key=key)


def _head_to_head(group: list[int], matches: list[MatchResult]) -> dict[int, _Totals] | None:
    """Return the mini-league totals over ``group``, or ``None`` when the group is incomplete.

    A group is complete when every ordered pair inside it carries a result. The
    caller's results hold at most one result per ordered pair, so counting is
    enough: ``n * (n - 1)`` results among the ``n`` members, with a postponed or
    unplayed match counting as missing.
    """
    members = set(group)
    within = [
        match
        for match in matches
        if match.home_team_id in members and match.away_team_id in members
    ]
    if len(within) != len(group) * (len(group) - 1):
        return None
    totals = {team_id: _Totals() for team_id in group}
    for match in within:
        _add_match(totals[match.home_team_id], match.home_goals, match.away_goals)
        _add_match(totals[match.away_team_id], match.away_goals, match.home_goals)
    return totals


def _totals_of(totals: dict[int, _Totals], team_id: int) -> _Totals:
    """Return the counters of ``team_id``, raising when the team is not in the table."""
    try:
        return totals[team_id]
    except KeyError:
        raise ValueError(f"result names team {team_id}, which is not in teams") from None


def _add_match(totals: _Totals, goals_for: int, goals_against: int) -> None:
    """Add one match to ``totals``, seen from the team those goals belong to."""
    totals.played += 1
    totals.goals_for += goals_for
    totals.goals_against += goals_against
    if goals_for > goals_against:
        totals.won += 1
        totals.points += _WIN_POINTS
    elif goals_for == goals_against:
        totals.drawn += 1
        totals.points += _DRAW_POINTS
    else:
        totals.lost += 1


def _outcome(goals_for: int, goals_against: int) -> str:
    """Return ``"W"``, ``"D"`` or ``"L"`` from the point of view of ``goals_for``."""
    if goals_for > goals_against:
        return "W"
    if goals_for < goals_against:
        return "L"
    return "D"


def _form(history: list[tuple[datetime.date, str]]) -> tuple[str, ...]:
    """Return the last five outcomes, most recent first; equal dates keep input order."""
    latest_first = sorted(history, key=_kickoff_date, reverse=True)
    return tuple(outcome for _, outcome in latest_first[:_FORM_LENGTH])


def _kickoff_date(entry: tuple[datetime.date, str]) -> datetime.date:
    """Return the match date of one history entry."""
    return entry[0]
