"""Tests for :func:`lmi_core.analytics.history.summarise` and its helpers.

All data is synthetic and every expected number is computed by hand and written
out in the assertion, so a failure points at the rule the test pins rather than
at a difference between two runs of the same code. The fixtures mirror what the
future ``GET /v1/teams/{team_id}/history`` route will hand in: seasons in an
order the caller does not control, a season the club did not play, matches
without statistics rows, statistics rows without cards or expected points, and a
table position the club does not have. Each test is named after the rule it
covers, in the order of the module docstring.
"""

from lmi_core.analytics.history import (
    MetricDrop,
    SeasonInput,
    SeasonLine,
    SeasonMatch,
    TeamHistory,
    summarise,
)

LineValues = tuple[str | int | float | None, ...]


def played(
    goals_for: int,
    goals_against: int,
    *,
    xpts: float | None = None,
    shots_for: int | None = None,
    shots_against: int | None = None,
    sot_for: int | None = None,
    sot_against: int | None = None,
    cards_for: int | None = None,
    cards_against: int | None = None,
) -> SeasonMatch:
    """Build one synthetic played match, seen from the club's side."""
    return SeasonMatch(
        goals_for=goals_for,
        goals_against=goals_against,
        xpts=xpts,
        shots_for=shots_for,
        shots_against=shots_against,
        sot_for=sot_for,
        sot_against=sot_against,
        cards_for=cards_for,
        cards_against=cards_against,
    )


def season(
    code: str, start_year: int, position: int | None, matches: list[SeasonMatch]
) -> SeasonInput:
    """Build one synthetic season with its table row and the matches it played."""
    return SeasonInput(
        season=code,
        start_year=start_year,
        position=position,
        matches=tuple(matches),
    )


def line_values(line: SeasonLine) -> LineValues:
    """Return every field of a line in payload order, so one assertion covers all thirteen."""
    return (
        line.season,
        line.position,
        line.played,
        line.points,
        line.points_pg,
        line.goals_for_pg,
        line.goals_against_pg,
        line.shots_for_pg,
        line.shots_against_pg,
        line.sot_for_pg,
        line.sot_against_pg,
        line.cards_pg,
        line.xpts_pg,
    )


def three_seasons() -> list[SeasonInput]:
    """Return three synthetic seasons in an order the caller does not control.

    Chronologically, all with two matches a season:

    - ``2020-21``: two 2-1 wins, so points_pg 3.0, goals_for_pg 2.0,
      goals_against_pg 1.0, shots_for_pg 10.0, shots_against_pg 4.0, sot_for_pg
      5.0, sot_against_pg 2.0, cards_pg 1.0 and xpts_pg 2.0.
    - ``2021-22``: two 1-1 draws, so 1.0, 1.0, 1.0, 8.0, 5.0, 4.0, 3.0, 2.0
      and 1.5 in the same order.
    - ``2022-23``: a 1-2 and a 0-2 loss, so 0.0, 0.5, 2.0, 5.0, 7.0, 2.0, 5.0,
      4.0 and 0.5.
    """
    return [
        season(
            "2022-23",
            2022,
            20,
            [
                played(
                    1,
                    2,
                    xpts=0.5,
                    shots_for=5,
                    shots_against=7,
                    sot_for=2,
                    sot_against=5,
                    cards_for=4,
                    cards_against=1,
                ),
                played(
                    0,
                    2,
                    xpts=0.5,
                    shots_for=5,
                    shots_against=7,
                    sot_for=2,
                    sot_against=5,
                    cards_for=4,
                    cards_against=5,
                ),
            ],
        ),
        season(
            "2020-21",
            2020,
            1,
            [
                played(
                    2,
                    1,
                    xpts=2.0,
                    shots_for=10,
                    shots_against=4,
                    sot_for=5,
                    sot_against=2,
                    cards_for=1,
                    cards_against=2,
                ),
                played(
                    2,
                    1,
                    xpts=2.0,
                    shots_for=10,
                    shots_against=4,
                    sot_for=5,
                    sot_against=2,
                    cards_for=1,
                    cards_against=3,
                ),
            ],
        ),
        season(
            "2021-22",
            2021,
            5,
            [
                played(
                    1,
                    1,
                    xpts=1.5,
                    shots_for=8,
                    shots_against=5,
                    sot_for=4,
                    sot_against=3,
                    cards_for=2,
                    cards_against=2,
                ),
                played(
                    1,
                    1,
                    xpts=1.5,
                    shots_for=8,
                    shots_against=5,
                    sot_for=4,
                    sot_against=3,
                    cards_for=2,
                    cards_against=0,
                ),
            ],
        ),
    ]


def test_seasons_are_listed_ascending_by_start_year_whatever_the_input_order() -> None:
    unsorted = [
        season("2022-23", 2022, 4, [played(1, 0)]),
        season("2020-21", 2020, 8, [played(2, 0)]),
        season("2021-22", 2021, 6, [played(3, 1)]),
    ]

    history = summarise(unsorted)

    assert [line.season for line in history.seasons] == ["2020-21", "2021-22", "2022-23"]
    # The argument is ordered on a copy, never in place.
    assert [item.season for item in unsorted] == ["2022-23", "2020-21", "2021-22"]
    # The input is consumed once, so a one-shot iterable gives the same history.
    assert summarise(iter(unsorted)).seasons == history.seasons


def test_two_seasons_of_one_start_year_are_ordered_by_their_code() -> None:
    history = summarise(
        [
            season("2020-B", 2020, 3, [played(1, 0)]),
            season("2020-A", 2020, 5, [played(0, 1)]),
        ]
    )

    # The start year cannot order these two, so the code does, whatever order the
    # caller listed them in.
    assert [line.season for line in history.seasons] == ["2020-A", "2020-B"]


def test_a_season_line_counts_points_and_goals_and_passes_the_position_through() -> None:
    with_a_row = season(
        "2020-21",
        2020,
        7,
        [played(2, 1), played(1, 1), played(0, 3), played(3, 0)],
    )
    without_a_row = season("2021-22", 2021, None, [played(1, 0), played(0, 1)])

    history = summarise([without_a_row, with_a_row])

    # Two wins and a draw earn 7 points over 4 matches, which is 1.75 per game;
    # goals are 6 for and 5 against, so 1.5 and 1.25. No match carries a
    # statistic, so every optional average is None.
    assert line_values(history.seasons[0]) == (
        "2020-21",
        7,
        4,
        7,
        1.75,
        1.5,
        1.25,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    # A club with no table row has an unknown position, which stays None; a win
    # and a loss are 3 points over 2 matches, with one goal each way.
    assert line_values(history.seasons[1]) == (
        "2021-22",
        None,
        2,
        3,
        1.5,
        0.5,
        0.5,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def test_each_average_uses_only_the_matches_that_carry_its_value() -> None:
    matches = [
        # No statistics row at all: it counts in played, points and goals only.
        played(2, 0),
        # A full statistics row, with expected points and two cards of its own.
        played(
            3,
            0,
            xpts=1.2,
            shots_for=10,
            shots_against=4,
            sot_for=3,
            sot_against=1,
            cards_for=2,
            cards_against=3,
        ),
        # A statistics row without expected points and without cards, so it
        # counts in the four shot fields and in no other optional average.
        played(1, 3, shots_for=6, shots_against=8, sot_for=1, sot_against=4),
    ]

    line = summarise([season("2021-22", 2021, 12, matches)]).seasons[0]

    # Two wins and a loss earn 6 points over 3 matches: 2.0 per game. Goals are
    # 6 for and 3 against, so 2.0 and 1.0. Shots cover two matches: 16/2 and
    # 12/2. Shots on target cover the same two: 4/2 and 5/2. Cards cover one
    # match, and only the club's own two, so the average is 2.0 and not the 2.5
    # a mean over both sides would give. Expected points cover one match: 1.2.
    assert line_values(line) == (
        "2021-22",
        12,
        3,
        6,
        2.0,
        2.0,
        1.0,
        8.0,
        6.0,
        2.0,
        2.5,
        2.0,
        1.2,
    )


def test_a_statistic_no_match_carries_is_none_while_a_recorded_zero_is_zero() -> None:
    matches = [
        played(1, 0, cards_for=0),
        played(0, 0, cards_for=0),
    ]

    line = summarise([season("2020-21", 2020, 1, matches)]).seasons[0]

    # No match carries a shot or an expected-points value, so those four means
    # and xpts_pg are None. Every match records zero own cards, and both
    # matches record zero goals against, so those two averages are 0.0: a
    # recorded zero is a measurement, a missing row is not.
    assert line_values(line) == (
        "2020-21",
        1,
        2,
        4,
        2.0,
        0.5,
        0.0,
        None,
        None,
        None,
        None,
        0.0,
        None,
    )


def test_a_season_without_a_played_match_is_left_out() -> None:
    history = summarise(
        [
            season("2021-22", 2021, 20, []),
            season("2020-21", 2020, 8, [played(1, 0)]),
            season("2022-23", 2022, 2, []),
        ]
    )

    # Only the season the club played is listed, and alone it has no decline.
    assert [line.season for line in history.seasons] == ["2020-21"]
    assert history.largest_drop == ()


def test_no_season_gives_an_empty_history() -> None:
    assert summarise([]) == TeamHistory(seasons=(), largest_drop=())


def test_seasons_of_the_fixture_are_summarised_by_hand() -> None:
    history = summarise(three_seasons())

    assert [line.season for line in history.seasons] == ["2020-21", "2021-22", "2022-23"]
    # 2020-21: two 2-1 wins are 6 points over 2 matches.
    assert line_values(history.seasons[0]) == (
        "2020-21",
        1,
        2,
        6,
        3.0,
        2.0,
        1.0,
        10.0,
        4.0,
        5.0,
        2.0,
        1.0,
        2.0,
    )
    # 2021-22: two 1-1 draws are 2 points over 2 matches.
    assert line_values(history.seasons[1]) == (
        "2021-22",
        5,
        2,
        2,
        1.0,
        1.0,
        1.0,
        8.0,
        5.0,
        4.0,
        3.0,
        2.0,
        1.5,
    )
    # 2022-23: two losses are no point over 2 matches, and half a goal a game.
    assert line_values(history.seasons[2]) == (
        "2022-23",
        20,
        2,
        0,
        0.0,
        0.5,
        2.0,
        5.0,
        7.0,
        2.0,
        5.0,
        4.0,
        0.5,
    )


def test_largest_drop_names_the_steepest_decline_of_every_metric() -> None:
    history = summarise(three_seasons())

    # Every metric declined, so all nine are listed. Steepest first, then by
    # metric name where two deltas tie, and each entry names the pair it came
    # from, so a reader can tell the steepest of two declines from the first one:
    # - shots_for_pg fell 8.0 -> 5.0 for -3.0, after a first decline of -2.0;
    # - cards_pg 2.0 -> 4.0, points_pg 3.0 -> 1.0, shots_against_pg 5.0 -> 7.0,
    #   sot_for_pg 4.0 -> 2.0 and sot_against_pg 3.0 -> 5.0 are all -2.0;
    # - goals_against_pg 1.0 -> 2.0, goals_for_pg 2.0 -> 1.0 and xpts_pg
    #   2.0 -> 0.5 are all -1.0, and goals_for_pg declined -0.5 in the second
    #   pair, which is shallower than its first.
    assert history.largest_drop == (
        MetricDrop(metric="shots_for_pg", from_season="2021-22", to_season="2022-23", delta=-3.0),
        MetricDrop(metric="cards_pg", from_season="2021-22", to_season="2022-23", delta=-2.0),
        MetricDrop(metric="points_pg", from_season="2020-21", to_season="2021-22", delta=-2.0),
        MetricDrop(
            metric="shots_against_pg", from_season="2021-22", to_season="2022-23", delta=-2.0
        ),
        MetricDrop(metric="sot_against_pg", from_season="2021-22", to_season="2022-23", delta=-2.0),
        MetricDrop(metric="sot_for_pg", from_season="2021-22", to_season="2022-23", delta=-2.0),
        MetricDrop(
            metric="goals_against_pg", from_season="2021-22", to_season="2022-23", delta=-1.0
        ),
        MetricDrop(metric="goals_for_pg", from_season="2020-21", to_season="2021-22", delta=-1.0),
        MetricDrop(metric="xpts_pg", from_season="2021-22", to_season="2022-23", delta=-1.0),
    )


def test_the_steepest_of_three_declines_wins_and_a_none_pair_is_skipped() -> None:
    history = summarise(
        [
            season("2020-21", 2020, 4, [played(2, 0, xpts=2.0), played(2, 0, xpts=2.0)]),
            season("2021-22", 2021, 15, [played(1, 1), played(1, 1)]),
        ]
    )

    # Points fall 3.0 -> 1.0, goals for 2.0 -> 1.0 and goals against 0.0 -> 1.0,
    # which is a decline because conceding more is worse. Expected points are
    # known in the first season and unknown in the second, so that pair is
    # skipped instead of being read as a fall to zero; the shot fields are
    # unknown on both sides and are skipped too.
    assert history.largest_drop == (
        MetricDrop(metric="points_pg", from_season="2020-21", to_season="2021-22", delta=-2.0),
        MetricDrop(
            metric="goals_against_pg", from_season="2020-21", to_season="2021-22", delta=-1.0
        ),
        MetricDrop(metric="goals_for_pg", from_season="2020-21", to_season="2021-22", delta=-1.0),
    )


def test_two_pairs_with_the_same_decline_keep_the_earlier_one() -> None:
    history = summarise(
        [
            season("2020-21", 2020, 5, [played(1, 0), played(1, 0)]),
            season("2021-22", 2021, 9, [played(1, 0), played(0, 0)]),
            season("2022-23", 2022, 8, [played(0, 0), played(0, 0)]),
        ]
    )

    # Points per game fall 3.0 -> 2.0 -> 1.0 and goals for 1.0 -> 0.5 -> 0.0, so
    # both pairs of each metric decline by the same amount. The earlier pair is
    # the one kept, which makes the served history independent of how the pairs
    # were visited.
    assert history.largest_drop == (
        MetricDrop(metric="points_pg", from_season="2020-21", to_season="2021-22", delta=-1.0),
        MetricDrop(metric="goals_for_pg", from_season="2020-21", to_season="2021-22", delta=-0.5),
    )


def test_a_metric_that_only_improved_is_left_out() -> None:
    history = summarise(
        [
            season("2020-21", 2020, 18, [played(0, 1), played(0, 1)]),
            season("2021-22", 2021, 3, [played(2, 0), played(2, 0)]),
        ]
    )

    # Points 0.0 -> 3.0, goals for 0.0 -> 2.0 and goals against 1.0 -> 0.0 all
    # moved the club's way, so nothing declined and no entry is served.
    assert history.largest_drop == ()


def test_one_season_has_no_decline_because_a_drop_needs_two() -> None:
    history = summarise([season("2020-21", 2020, 1, [played(2, 1), played(0, 3)])])

    assert len(history.seasons) == 1
    assert history.largest_drop == ()
