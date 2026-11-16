"""Tests for :func:`lmi_core.analytics.season_stats.summarise` and its helpers.

All data is synthetic and every expected number is computed by hand and written
out in the assertion, so a failure points at the rule the test pins rather than
at a difference between two runs of the same code. The season fixture mirrors
what the future ``GET /v1/teams/{team_id}/season-stats`` route will hand in:
mixed venues, a match with no statistics row, a statistics row without xG, and
matches whose fixture carries no pre-closing price. Each test is named after the
rule it covers, in the order of the module docstring.
"""

import datetime

import pytest
from lmi_core.analytics.season_stats import (
    MatchLine,
    MatchRecord,
    PlayedMatch,
    RollingForm,
    SideLine,
    StatLine,
    StatValues,
    Venue,
    average_line,
    market_expected_points,
    match_points,
    match_record,
    summarise,
    tier_members,
    tier_records,
)

TEAM = 100

MatchValues = tuple[int, Venue, int, float | None, int, int, int | None, int | None]


def played(
    match_id: int,
    kickoff_date: datetime.date,
    venue: Venue,
    opponent_id: int,
    goals_for: int,
    goals_against: int,
    *,
    xpts: float | None = None,
    stats_for: StatValues | None = None,
    stats_against: StatValues | None = None,
) -> PlayedMatch:
    """Build one synthetic match from ``TEAM``'s point of view."""
    return PlayedMatch(
        match_id=match_id,
        kickoff_date=kickoff_date,
        venue=venue,
        opponent_id=opponent_id,
        goals_for=goals_for,
        goals_against=goals_against,
        xpts=xpts,
        stats_for=stats_for,
        stats_against=stats_against,
    )


def season() -> list[PlayedMatch]:
    """Return six matches of ``TEAM`` in an order that is deliberately not chronological.

    Chronologically:
        1. away 1-2, no xPts, statistics both sides        -> 0 points
        2. home 3-1, xPts 2.4, no statistics row           -> 3 points
        3. home 2-0, xPts 1.0, statistics without xG       -> 3 points
        4. away 2-2, no xPts, statistics both sides        -> 1 point
        5. home 2-0, xPts 2.6, statistics both sides       -> 3 points
        6. away 2-1, xPts 0.4, statistics both sides       -> 3 points
    """
    return [
        played(
            504,
            datetime.date(2026, 1, 26),
            "away",
            204,
            2,
            2,
            stats_for=StatValues(
                shots=8,
                shots_on_target=4,
                fouls=9,
                corners=5,
                yellow=0,
                red=0,
                xg=1.2,
            ),
            stats_against=StatValues(
                shots=11,
                shots_on_target=4,
                fouls=11,
                corners=4,
                yellow=1,
                red=0,
                xg=1.4,
            ),
        ),
        played(
            501,
            datetime.date(2026, 1, 5),
            "away",
            201,
            1,
            2,
            stats_for=StatValues(
                shots=5,
                shots_on_target=2,
                fouls=10,
                corners=3,
                yellow=1,
                red=0,
                xg=0.8,
            ),
            stats_against=StatValues(
                shots=12,
                shots_on_target=5,
                fouls=8,
                corners=6,
                yellow=2,
                red=0,
                xg=1.6,
            ),
        ),
        played(
            506,
            datetime.date(2026, 2, 9),
            "away",
            206,
            2,
            1,
            xpts=0.4,
            stats_for=StatValues(
                shots=6,
                shots_on_target=2,
                fouls=15,
                corners=2,
                yellow=3,
                red=1,
                xg=0.9,
            ),
            stats_against=StatValues(
                shots=9,
                shots_on_target=6,
                fouls=8,
                corners=5,
                yellow=1,
                red=0,
                xg=2.5,
            ),
        ),
        played(502, datetime.date(2026, 1, 12), "home", 202, 3, 1, xpts=2.4),
        played(
            505,
            datetime.date(2026, 2, 2),
            "home",
            205,
            2,
            0,
            xpts=2.6,
            stats_for=StatValues(
                shots=14,
                shots_on_target=6,
                fouls=7,
                corners=8,
                yellow=1,
                red=0,
                xg=2.1,
            ),
            stats_against=StatValues(
                shots=4,
                shots_on_target=0,
                fouls=13,
                corners=1,
                yellow=2,
                red=1,
                xg=0.3,
            ),
        ),
        played(
            503,
            datetime.date(2026, 1, 19),
            "home",
            203,
            2,
            0,
            xpts=1.0,
            stats_for=StatValues(
                shots=10,
                shots_on_target=3,
                fouls=12,
                corners=7,
                yellow=2,
                red=0,
            ),
            stats_against=StatValues(
                shots=6,
                shots_on_target=1,
                fouls=14,
                corners=2,
                yellow=3,
                red=0,
            ),
        ),
    ]


def league_lines() -> list[SideLine]:
    """Return four league-wide per-side lines, built by hand.

    Averages over them are goals 1.0, shots 6.0, shots on target 2.0, fouls
    10.0, corners 4.0, yellow 1.0, red 0.0 and xG 0.95: the line with no
    statistics and the line without xG stay out of those two means only.
    """
    return [
        SideLine(
            goals=2,
            stats=StatValues(
                shots=9,
                shots_on_target=3,
                fouls=9,
                corners=6,
                yellow=2,
                red=0,
                xg=1.5,
            ),
        ),
        SideLine(
            goals=0,
            stats=StatValues(
                shots=3,
                shots_on_target=0,
                fouls=12,
                corners=3,
                yellow=1,
                red=0,
                xg=0.4,
            ),
        ),
        SideLine(goals=1, stats=None),
        SideLine(
            goals=1,
            stats=StatValues(
                shots=6,
                shots_on_target=3,
                fouls=9,
                corners=3,
                yellow=0,
                red=0,
            ),
        ),
    ]


def stat_values(line: StatLine) -> tuple[float | None, ...]:
    """Return the fields of a line in order, so one assertion covers all eight."""
    return (
        line.goals,
        line.shots,
        line.shots_on_target,
        line.fouls,
        line.corners,
        line.yellow,
        line.red,
        line.xg,
    )


def rolling_form(form: RollingForm) -> tuple[float | None, ...]:
    """Return the fields of a rolling form in order."""
    return (form.points_pg, form.xpts_pg, form.goals_for_pg, form.goals_against_pg)


def match_values(matches: tuple[MatchLine, ...]) -> list[MatchValues]:
    """Return the season rows without their rolling form, for the hand-written table."""
    rows: list[MatchValues] = []
    for row in matches:
        rows.append(
            (
                row.match_id,
                row.venue,
                row.points,
                row.xpts,
                row.goals_for,
                row.goals_against,
                row.shots_for,
                row.shots_against,
            )
        )
    return rows


def test_match_points_awards_three_for_a_win_one_for_a_draw_and_none_for_a_loss() -> None:
    assert match_points(2, 1) == 3
    assert match_points(1, 1) == 1
    assert match_points(0, 2) == 0


def test_match_record_counts_results_and_has_no_points_per_game_over_no_match() -> None:
    matches = [
        played(1, datetime.date(2026, 1, 1), "home", 201, 2, 1),
        played(2, datetime.date(2026, 1, 8), "away", 202, 1, 1),
        played(3, datetime.date(2026, 1, 15), "home", 203, 0, 3),
        played(4, datetime.date(2026, 1, 22), "away", 204, 3, 0),
    ]

    record = match_record(matches)

    # Two wins and one draw earn 7 points over 4 matches; goals are 6 for, 5 against.
    assert record == MatchRecord(
        played=4,
        won=2,
        drawn=1,
        lost=1,
        goals_for=6,
        goals_against=5,
        points=7,
        points_per_game=1.75,
    )
    assert match_record([]) == MatchRecord(
        played=0,
        won=0,
        drawn=0,
        lost=0,
        goals_for=0,
        goals_against=0,
        points=0,
        points_per_game=None,
    )


def test_market_expected_points_is_three_p_win_plus_p_draw_seen_from_the_venue() -> None:
    # 2.0 / 3.5 / 4.0: the inverse prices sum to 29/28, so p_home is 14/29 and
    # p_draw is 8/29. The home side earns 3 * 14/29 + 8/29 = 50/29.
    price = (2.0, 3.5, 4.0)
    assert market_expected_points(price, venue="home") == pytest.approx(50 / 29)

    # The same price read from the other venue rewards the other outcome: p_away
    # is 7/29, so a visitor earns 3 * 7/29 + 8/29 = 29/29, which is not 50/29.
    assert market_expected_points(price, venue="away") == pytest.approx(29 / 29)

    # 3.0 / 3.2 / 2.4: the inverse prices sum to 51/48, so p_away is 20/51 and
    # p_draw is 15/51. A visitor earns 3 * 20/51 + 15/51 = 25/17.
    assert market_expected_points((3.0, 3.2, 2.4), venue="away") == pytest.approx(25 / 17)


def test_tier_members_cuts_a_ranked_table_into_three_ascending_tiers() -> None:
    # Six clubs, so ceil(6 / 3) = 2 per tier. The mapping is team id -> position
    # and its key order is not the ranked order, which proves the position
    # decides; every tier is then ascending by identifier, not by rank.
    six = {14: 1, 11: 2, 15: 3, 12: 4, 16: 5, 13: 6}
    assert tier_members(six) == {
        "top": (11, 14),
        "middle": (12, 15),
        "bottom": (13, 16),
    }

    # Four clubs: ceil(4 / 3) = 2 fills the top and the middle, so the bottom is
    # an empty tuple and never a missing key.
    four = {24: 1, 21: 2, 23: 3, 22: 4}
    assert tier_members(four) == {"top": (21, 24), "middle": (22, 23), "bottom": ()}


def test_tier_records_count_only_the_matches_against_the_members_of_a_tier() -> None:
    matches = [
        played(1, datetime.date(2026, 1, 4), "home", 11, 2, 0),
        played(2, datetime.date(2026, 1, 11), "away", 14, 1, 1),
        played(3, datetime.date(2026, 1, 18), "home", 12, 0, 3),
        played(4, datetime.date(2026, 1, 25), "away", 16, 3, 1),
        played(5, datetime.date(2026, 2, 1), "home", 13, 1, 0),
    ]

    records = tier_records(
        matches,
        members={"top": (11, 14), "middle": (12, 15), "bottom": (16, 13)},
    )

    # Against the top tier: a home win and an away draw, 3 goals for 1 against
    # over 2 matches, which is 4 points and 2.0 per game.
    assert records["top"] == MatchRecord(
        played=2,
        won=1,
        drawn=1,
        lost=0,
        goals_for=3,
        goals_against=1,
        points=4,
        points_per_game=2.0,
    )
    # Against the middle: the home loss to 12 alone; club 15 was never faced.
    assert records["middle"] == MatchRecord(
        played=1,
        won=0,
        drawn=0,
        lost=1,
        goals_for=0,
        goals_against=3,
        points=0,
        points_per_game=0.0,
    )
    # Against the bottom: an away win and a home win, whatever the venue.
    assert records["bottom"] == MatchRecord(
        played=2,
        won=2,
        drawn=0,
        lost=0,
        goals_for=4,
        goals_against=1,
        points=6,
        points_per_game=3.0,
    )

    # A tier the caller left out is served as a record of zeros without a
    # made-up average, so a client always paints three tiers.
    empty = tier_records(matches, members={"top": (11, 14), "middle": (12,)})
    assert empty["bottom"] == MatchRecord(
        played=0,
        won=0,
        drawn=0,
        lost=0,
        goals_for=0,
        goals_against=0,
        points=0,
        points_per_game=None,
    )


def test_average_line_averages_each_statistic_over_the_lines_that_carry_it() -> None:
    lines = [
        SideLine(goals=1, stats=StatValues(shots=4, red=0, xg=0.5)),
        SideLine(goals=3, stats=StatValues(shots=8)),
        SideLine(goals=2, stats=None),
    ]

    line = average_line(lines)

    # Goals cover all three lines: 6 / 3. Shots cover two: 12 / 2. The only red
    # value is a recorded zero, which averages to 0.0 and must not become None.
    assert stat_values(line) == (2.0, 6.0, None, None, None, None, 0.0, 0.5)


def test_average_line_of_no_lines_is_all_none() -> None:
    assert stat_values(average_line([])) == (None,) * 8


def test_summarise_counts_and_averages_a_hand_computed_season() -> None:
    stats = summarise(season(), league=league_lines())

    # 4 wins, 1 draw, 1 loss: 13 points over 6 matches.
    assert stats.played == 6
    assert stats.points == 13
    # Four matches carry xPts: 2.4 + 1.0 + 2.6 + 0.4 = 6.4.
    assert stats.xpts_market == 6.4
    assert stats.xpts_coverage == 4
    # Five matches carry a statistics row; four carry xG on at least one side.
    assert stats.stat_coverage == 5
    assert stats.xg_coverage == 4

    # For: goals 12/6; shots 43/5, on target 17/5, fouls 53/5, corners 25/5,
    # yellow 7/5, red 1/5; xG over the four rows that carry it: 5.0/4.
    assert stat_values(stats.per_match_for) == (2.0, 8.6, 3.4, 10.6, 5.0, 1.4, 0.2, 1.25)
    # Against: goals 6/6; shots 42/5, on target 16/5, fouls 54/5, corners 18/5,
    # yellow 9/5, red 1/5; xG 5.8/4.
    assert stat_values(stats.per_match_against) == (1.0, 8.4, 3.2, 10.8, 3.6, 1.8, 0.2, 1.45)
    assert stat_values(stats.league_avg) == (1.0, 6.0, 2.0, 10.0, 4.0, 1.0, 0.0, 0.95)


def test_summarise_orders_matches_and_builds_each_hand_computed_row() -> None:
    matches = season()

    stats = summarise(matches, league=league_lines())

    # Chronological order by (date, match id), whatever order the caller passed.
    assert match_values(stats.matches) == [
        (501, "away", 0, None, 1, 2, 5, 12),
        (502, "home", 3, 2.4, 3, 1, None, None),
        (503, "home", 3, 1.0, 2, 0, 10, 6),
        (504, "away", 1, None, 2, 2, 8, 11),
        (505, "home", 3, 2.6, 2, 0, 14, 4),
        (506, "away", 3, 0.4, 2, 1, 6, 9),
    ]
    # The caller's list is sorted on a copy, never in place.
    assert [match.match_id for match in matches] == [504, 501, 506, 502, 505, 503]


def test_rolling_form_is_all_none_for_the_first_match() -> None:
    stats = summarise(season(), league=[])

    assert stats.matches[0].rolling5 == RollingForm(
        points_pg=None,
        xpts_pg=None,
        goals_for_pg=None,
        goals_against_pg=None,
    )


def test_rolling_form_averages_only_the_matches_before_each_match() -> None:
    stats = summarise(season(), league=[])

    rollings = [row.rolling5 for row in stats.matches]

    # Match 2's window is match 1 alone: 0 points, no odds, 1 goal for, 2 against.
    assert rolling_form(rollings[1]) == (0.0, None, 1.0, 2.0)
    # Match 3: 0+3 over two matches, xPts 2.4 from match 2 alone, goals 4/2, 3/2.
    assert rolling_form(rollings[2]) == (1.5, 2.4, 2.0, 1.5)
    # Match 4: 0+3+3 over three, xPts (2.4+1.0)/2, goals 6/3, 3/3.
    assert rolling_form(rollings[3]) == (2.0, 1.7, 2.0, 1.0)
    # Match 5: 0+3+3+1 over four, xPts still (2.4+1.0)/2, goals 8/4, 5/4.
    assert rolling_form(rollings[4]) == (1.75, 1.7, 2.0, 1.25)
    # Match 6 has all five earlier matches in its window: points 0+3+3+1+3 over
    # five; xPts (2.4+1.0+2.6)/3 because match 4 carries no odds; goals 10/5,
    # against 5/5.
    assert rolling_form(rollings[5]) == (2.0, 2.0, 2.0, 1.0)


def test_rolling_form_keeps_only_the_five_most_recent_matches() -> None:
    day = datetime.date(2026, 2, 16)
    matches = [*season(), played(507, day, "home", 207, 4, 0, xpts=2.8)]

    stats = summarise(matches, league=[])

    # The seventh match's window holds matches 2..6 and leaves match 1 out, so
    # it is five matches wide, not six: points 3+3+1+3+3 over five is 2.6, while
    # a six-match window starting at match 1 would give 10/6. xPts
    # (2.4+1.0+2.6+0.4)/4 is 1.6; goals 11/5, against 4/5.
    assert rolling_form(stats.matches[6].rolling5) == (2.6, 1.6, 2.2, 0.8)


def test_a_same_date_fixture_never_enters_the_other_window() -> None:
    day = datetime.date(2026, 3, 1)
    # Passed latest id first: the sort key must use match id, not input order.
    matches = [
        played(40, day, "away", 301, 0, 2, xpts=None),
        played(39, day, "home", 300, 1, 0, xpts=2.0),
    ]

    stats = summarise(matches, league=[])

    assert [row.match_id for row in stats.matches] == [39, 40]
    assert [row.venue for row in stats.matches] == ["home", "away"]
    # A stored date cannot order two fixtures of one day, so neither sibling is
    # form for the other: both windows are empty, however the identifiers sort.
    assert rolling_form(stats.matches[0].rolling5) == (None, None, None, None)
    assert rolling_form(stats.matches[1].rolling5) == (None, None, None, None)
    assert [match.match_id for match in matches] == [40, 39]
