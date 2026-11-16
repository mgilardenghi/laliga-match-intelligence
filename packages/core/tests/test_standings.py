"""Tests for :func:`lmi_core.standings.compute_standings`.

All data is synthetic: club names such as ``"Test FC Alpha"`` and hand-built
results. Every expected table is computed by hand and written out above the
fixtures, so a failing assertion points at the rule the test pins. Each test is
named after the rule it covers, in the order of the module docstring.
"""

import dataclasses
import datetime

import pytest
from lmi_core.standings import (
    MatchResult,
    StandingRow,
    compute_standings,
    outcome,
    points_for,
)

ALPHA = 1
BRAVO = 2
CHARLIE = 3
DELTA = 4

TEAMS = {
    ALPHA: "Test FC Alpha",
    BRAVO: "Test FC Bravo",
    CHARLIE: "Test FC Charlie",
    DELTA: "Test FC Delta",
}


def match(home: int, away: int, home_goals: int, away_goals: int, day: int = 1) -> MatchResult:
    """Build a synthetic result played on ``day`` January 2026."""
    return MatchResult(
        home_team_id=home,
        away_team_id=away,
        home_goals=home_goals,
        away_goals=away_goals,
        kickoff_date=datetime.date(2026, 1, day),
    )


def summary(table: list[StandingRow]) -> list[tuple[int, ...]]:
    """Return ``(position, team, played, won, drawn, lost, gf, ga, gd, points)`` per row."""
    return [
        (
            row.position,
            row.team_id,
            row.played,
            row.won,
            row.drawn,
            row.lost,
            row.goals_for,
            row.goals_against,
            row.goal_difference,
            row.points,
        )
        for row in table
    ]


def placements(table: list[StandingRow]) -> list[int]:
    """Return the team ids in table order, to compare against a hand-written ranking."""
    return [row.team_id for row in table]


def row_of(table: list[StandingRow], team_id: int) -> StandingRow:
    """Return the row of ``team_id``."""
    return next(row for row in table if row.team_id == team_id)


def test_a_small_round_robin_reports_points_and_goal_counts() -> None:
    # Single round robin, three matchdays, no level group at all.
    # Charlie: 2W 1D -> 7 points, 5-1. Alpha: 2W 1L -> 6 points, 3-3.
    # Delta: 2D 1L -> 2 points, 3-4. Bravo: 1D 2L -> 1 point, 2-5.
    results = [
        match(ALPHA, BRAVO, 2, 0),
        match(CHARLIE, DELTA, 1, 1),
        match(ALPHA, CHARLIE, 0, 3),
        match(BRAVO, DELTA, 2, 2),
        match(ALPHA, DELTA, 1, 0),
        match(BRAVO, CHARLIE, 0, 1),
    ]

    table = compute_standings(TEAMS, results)

    assert summary(table) == [
        (1, CHARLIE, 3, 2, 1, 0, 5, 1, 4, 7),
        (2, ALPHA, 3, 2, 0, 1, 3, 3, 0, 6),
        (3, DELTA, 3, 0, 2, 1, 3, 4, -1, 2),
        (4, BRAVO, 3, 0, 1, 2, 2, 5, -3, 1),
    ]


def test_two_teams_level_with_both_legs_played_use_head_to_head_goal_difference() -> None:
    # Bravo and Alpha are level on 6 points and have played both legs:
    #   Alpha 1-0 Bravo and Bravo 3-1 Alpha, so head-to-head Alpha 2-3 Bravo.
    # Head-to-head goal difference favours Bravo (-1 against +1), while overall
    # goal difference favours Alpha (+4 against +2). Bravo must come first.
    # Charlie and Delta are level on 0 points, so goal difference ranks Delta
    # third and Charlie fourth.
    results = [
        match(ALPHA, BRAVO, 1, 0),
        match(BRAVO, ALPHA, 3, 1),
        match(ALPHA, CHARLIE, 5, 0),
        match(BRAVO, CHARLIE, 1, 0),
    ]

    table = compute_standings(TEAMS, results)

    assert placements(table) == [BRAVO, ALPHA, DELTA, CHARLIE]
    assert row_of(table, BRAVO).points == 6
    assert row_of(table, ALPHA).points == 6
    assert row_of(table, BRAVO).goal_difference == 2
    assert row_of(table, ALPHA).goal_difference == 4


def test_two_teams_level_with_one_leg_played_use_overall_goal_difference() -> None:
    # Single round robin, so Alpha and Bravo meet once: their head-to-head is
    # incomplete and cannot decide. Alpha beat Bravo 1-0, but overall goal
    # difference is Alpha 0 against Bravo +2, so Bravo comes first anyway.
    # Charlie tops the table with 5 points, Delta is last on 2.
    results = [
        match(ALPHA, BRAVO, 1, 0),
        match(CHARLIE, DELTA, 0, 0),
        match(ALPHA, CHARLIE, 0, 1),
        match(BRAVO, DELTA, 3, 0),
        match(ALPHA, DELTA, 0, 0),
        match(BRAVO, CHARLIE, 0, 0),
    ]

    table = compute_standings(TEAMS, results)

    assert placements(table) == [CHARLIE, BRAVO, ALPHA, DELTA]
    assert summary(table) == [
        (1, CHARLIE, 3, 1, 2, 0, 1, 0, 1, 5),
        (2, BRAVO, 3, 1, 1, 1, 3, 1, 2, 4),
        (3, ALPHA, 3, 1, 1, 1, 1, 1, 0, 4),
        (4, DELTA, 3, 0, 2, 1, 0, 3, -3, 2),
    ]


def test_level_head_to_head_goal_difference_never_counts_away_goals() -> None:
    # Bravo and Alpha are level on 6 points and have played both legs:
    #   Alpha 2-1 Bravo and Bravo 3-2 Alpha, so the aggregate is 4-4 and both
    #   head-to-head goal differences are 0, as are the head-to-head points.
    # Alpha scored 2 away goals to Bravo's 1, so an away-goals rule would rank
    # Alpha first; the fall-through to overall goal difference ranks Bravo
    # first (+4 against +1) instead. Delta, with no result, is third.
    results = [
        match(ALPHA, BRAVO, 2, 1),
        match(BRAVO, ALPHA, 3, 2),
        match(ALPHA, CHARLIE, 1, 0),
        match(BRAVO, CHARLIE, 4, 0),
    ]

    table = compute_standings(TEAMS, results)

    assert placements(table) == [BRAVO, ALPHA, DELTA, CHARLIE]
    assert row_of(table, BRAVO).goal_difference == 4
    assert row_of(table, ALPHA).goal_difference == 1
    assert row_of(table, BRAVO).points == row_of(table, ALPHA).points == 6


def test_a_complete_three_team_group_is_decided_by_mini_league_points() -> None:
    # Alpha, Bravo and Charlie are level on 10 points and every ordered pair
    # inside the group has a result (6 results). The mini-league gives
    #   Alpha 7 points, Bravo 6, Charlie 4,
    # so the mini-league ranks Alpha, Bravo, Charlie. Overall goal difference
    # would rank them Charlie (+4), Alpha (+3), Bravo (+1), so the head-to-head
    # points are what put Alpha first. Delta is last on 1 point.
    results = [
        match(ALPHA, CHARLIE, 1, 1),
        match(CHARLIE, ALPHA, 0, 2),
        match(ALPHA, BRAVO, 1, 0),
        match(BRAVO, ALPHA, 2, 1),
        match(BRAVO, CHARLIE, 1, 0),
        match(CHARLIE, BRAVO, 2, 1),
        match(ALPHA, DELTA, 1, 0),
        match(BRAVO, DELTA, 1, 0),
        match(DELTA, BRAVO, 1, 1),
        match(CHARLIE, DELTA, 3, 0),
        match(DELTA, CHARLIE, 0, 3),
    ]

    table = compute_standings(TEAMS, results)

    assert placements(table) == [ALPHA, BRAVO, CHARLIE, DELTA]
    assert [row.points for row in table] == [10, 10, 10, 1]
    assert [row.goal_difference for row in table] == [3, 1, 4, -8]


def test_equal_mini_league_points_fall_back_to_mini_league_goal_difference() -> None:
    # Alpha, Bravo and Charlie are level on 8 points and complete in the
    # mini-league: each wins one, draws two and loses one, so all three have 5
    # mini-league points. Mini-league goal difference is
    #   Alpha +4, Bravo -1, Charlie -3,
    # which ranks Alpha, Bravo, Charlie although overall goal difference is
    # Charlie +6, Alpha +5, Bravo 0. Delta is last.
    results = [
        match(ALPHA, BRAVO, 5, 0),
        match(BRAVO, ALPHA, 0, 0),
        match(BRAVO, CHARLIE, 4, 0),
        match(CHARLIE, BRAVO, 0, 0),
        match(CHARLIE, ALPHA, 1, 0),
        match(ALPHA, CHARLIE, 0, 0),
        match(ALPHA, DELTA, 1, 0),
        match(BRAVO, DELTA, 1, 0),
        match(CHARLIE, DELTA, 9, 0),
    ]

    table = compute_standings(TEAMS, results)

    assert placements(table) == [ALPHA, BRAVO, CHARLIE, DELTA]
    assert [row.points for row in table] == [8, 8, 8, 0]
    assert [row.goal_difference for row in table] == [5, 0, 6, -11]


def test_a_level_sub_group_falls_through_without_recursion() -> None:
    # Alpha, Bravo and Charlie are level on 8 points and complete in the
    # mini-league: Charlie 8 points, Alpha 4, Bravo 4. Alpha and Bravo are also
    # level on mini-league goal difference (-2 each), so their sub-group is NOT
    # re-compared head to head. The fall-through to overall goal difference puts
    # Charlie first (+4), then Bravo (+3) and Alpha (-1). Recursion would rank
    # Alpha above Bravo, because Alpha beat Bravo 2-0 at home and lost 0-1 away.
    # Delta is last on 2 points.
    results = [
        match(ALPHA, BRAVO, 2, 0),
        match(BRAVO, ALPHA, 1, 0),
        match(ALPHA, CHARLIE, 1, 1),
        match(CHARLIE, ALPHA, 3, 0),
        match(BRAVO, CHARLIE, 2, 2),
        match(CHARLIE, BRAVO, 1, 0),
        match(ALPHA, DELTA, 1, 0),
        match(DELTA, ALPHA, 0, 0),
        match(BRAVO, DELTA, 5, 0),
        match(DELTA, BRAVO, 0, 0),
    ]

    table = compute_standings(TEAMS, results)

    assert placements(table) == [CHARLIE, BRAVO, ALPHA, DELTA]
    assert [row.points for row in table] == [8, 8, 8, 2]
    assert [row.goal_difference for row in table] == [4, 3, -1, -6]


def test_a_full_tie_is_broken_by_team_name_with_unique_positions() -> None:
    # Ids run one way and names the other, so the name decides and the id does
    # not. Nothing has been played: every numeric criterion is level.
    teams = {
        ALPHA: "Test FC Delta",
        BRAVO: "Test FC Charlie",
        CHARLIE: "Test FC Bravo",
        DELTA: "Test FC Alpha",
    }

    table = compute_standings(teams, [])

    assert placements(table) == [DELTA, CHARLIE, BRAVO, ALPHA]
    assert [row.position for row in table] == [1, 2, 3, 4]
    assert [row.form for row in table] == [(), (), (), ()]

    # Club names are unique in the database, but the id still ends the chain.
    same_name = {7: "Test FC Echo", 5: "Test FC Echo"}

    assert placements(compute_standings(same_name, [])) == [5, 7]


def test_a_team_without_results_keeps_a_row_of_zeros() -> None:
    # Alpha beat Bravo 1-0, so the three idle teams are level on 0 points with
    # an incomplete head-to-head: Charlie and Delta have a goal difference of 0
    # against Bravo's -1, and Delta's name sorts after Charlie's.
    table = compute_standings(TEAMS, [match(ALPHA, BRAVO, 1, 0)])

    assert placements(table) == [ALPHA, CHARLIE, DELTA, BRAVO]
    idle = row_of(table, DELTA)
    assert idle.position == 3
    assert (idle.played, idle.won, idle.drawn, idle.lost) == (0, 0, 0, 0)
    assert (idle.goals_for, idle.goals_against) == (0, 0)
    assert (idle.goal_difference, idle.points) == (0, 0)
    assert idle.form == ()


def test_form_lists_the_last_five_results_most_recent_first() -> None:
    # Alpha plays six matches, one per day:
    #   day 1 at home 0-1 -> L   day 2 away 2-0 -> W   day 3 at home 1-1 -> D
    #   day 4 away 1-1 -> D      day 5 at home 2-0 -> W day 6 away 0-3 -> L
    # The last five, most recent first, drop day 1: L, W, D, D, W.
    teams = {team_id: f"Test FC Form {team_id}" for team_id in range(1, 8)}
    results = [
        match(1, 2, 0, 1, day=1),
        match(3, 1, 0, 2, day=2),
        match(1, 4, 1, 1, day=3),
        match(5, 1, 1, 1, day=4),
        match(1, 6, 2, 0, day=5),
        match(7, 1, 3, 0, day=6),
    ]

    table = compute_standings(teams, results)

    assert row_of(table, 1).form == ("L", "W", "D", "D", "W")
    assert row_of(table, 2).form == ("W",)

    # Equal kickoff dates keep input order: both matches are on day 10.
    same_day = [match(ALPHA, BRAVO, 1, 0, day=10), match(ALPHA, CHARLIE, 0, 1, day=10)]

    assert row_of(compute_standings(TEAMS, same_day), ALPHA).form == ("W", "L")


def test_an_unknown_team_and_a_self_match_raise() -> None:
    with pytest.raises(ValueError, match="not in teams"):
        compute_standings(TEAMS, [match(ALPHA, 99, 1, 0)])
    with pytest.raises(ValueError, match="not in teams"):
        compute_standings(TEAMS, [match(99, ALPHA, 1, 0)])
    with pytest.raises(ValueError, match="cannot play itself"):
        compute_standings(TEAMS, [match(ALPHA, ALPHA, 1, 0)])


def test_outcome_and_points_for_are_the_single_result_rule() -> None:
    # The same 2-0 score, seen from each side, yields W and L; a level score is
    # D for both sides. A result earns three, one or no points.
    assert outcome(2, 0) == "W"
    assert outcome(0, 2) == "L"
    assert outcome(2, 2) == "D"
    assert [points_for("W"), points_for("D"), points_for("L")] == [3, 1, 0]


def test_the_public_dataclasses_keep_the_specified_shape() -> None:
    # M3 mirrors these fields in JSON and M4 reads rows from simulated seasons,
    # so the names, their order, and the frozen slots are the contract, not an
    # implementation detail.
    assert [field.name for field in dataclasses.fields(MatchResult)] == [
        "home_team_id",
        "away_team_id",
        "home_goals",
        "away_goals",
        "kickoff_date",
    ]
    assert [field.name for field in dataclasses.fields(StandingRow)] == [
        "position",
        "team_id",
        "played",
        "won",
        "drawn",
        "lost",
        "goals_for",
        "goals_against",
        "goal_difference",
        "points",
        "form",
    ]

    result = match(ALPHA, BRAVO, 1, 0)
    row = row_of(compute_standings(TEAMS, [result]), ALPHA)
    for instance, attribute in ((result, "home_goals"), (row, "points")):
        assert not hasattr(instance, "__dict__")
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(instance, attribute, 0)


def test_an_empty_input_returns_an_empty_table() -> None:
    assert compute_standings({}, []) == []
