"""Tests for :mod:`lmi_core.analytics.relegation` and its three rules.

All data is synthetic and every expected number is computed by hand and written
out in the assertion, so a failure points at the rule the test pins rather than
at a difference between two runs of the same code. The fixtures mirror what the
future ``GET /v1/teams/{team_id}/relegation-race`` route will hand in: stored
seasons of the sizes and result counts the ingestion produces, a simulation
run of one club per league place, and the stored outcome counts of one club's
next match. Each test is named after the rule it covers, in the order of the
module docstring.
"""

import pytest
from lmi_core.analytics.relegation import (
    OutcomeCounts,
    RankedTeam,
    SeasonTable,
    SurvivalThreshold,
    nearest_rivals,
    next_match_swing,
    survival_threshold,
)

MATCH_ID = 500
"""The next match the swing tests read: the identifier every stored row carries."""


def points_table(seventeenth: int, *, eighteenth: int | None = None) -> tuple[int, ...]:
    """Return 20 descending point totals whose 17th place holds ``seventeenth``.

    Every place above the 17th is two points higher per place and every place
    below it two points lower, starting from ``eighteenth`` when a test needs an
    18th-place total of its own. The filler never enters an assertion: a test
    writes the total it expects to be read.
    """
    totals = [seventeenth + 2 * (17 - position) for position in range(1, 17)]
    totals.append(seventeenth)
    bottom = seventeenth - 2 if eighteenth is None else eighteenth
    totals.extend(bottom - 2 * (position - 18) for position in range(18, 21))
    return tuple(totals)


def stored_season(
    season: str,
    start_year: int,
    points: tuple[int, ...],
    *,
    n_teams: int | None = None,
    results_counted: int | None = None,
) -> SeasonTable:
    """Build one stored season, complete and 20 clubs wide unless a test says otherwise.

    ``n_teams`` defaults to the length of ``points`` and the result count to that
    league's full double round robin, so a test writes only the fields its rule is
    about.
    """
    size = len(points) if n_teams is None else n_teams
    counted = size * (size - 1) if results_counted is None else results_counted
    return SeasonTable(
        season=season,
        start_year=start_year,
        n_teams=size,
        results_counted=counted,
        points=points,
    )


def ranked(team_id: int, expected_position: float) -> RankedTeam:
    """Build one club of a simulation run, with the columns the race compares.

    Only ``team_id`` and ``expected_position`` decide a selection, so the other
    columns carry filler values that no test asserts on.
    """
    return RankedTeam(
        team_id=team_id,
        current_points=40,
        expected_points=44.5,
        expected_position=expected_position,
        p_relegation=0.25,
        points_p05=34,
        points_p95=55,
    )


def run_of_twenty() -> list[RankedTeam]:
    """Return a 20-club run whose expected positions run 1.0 up to 20.0.

    Club ``100 + n`` is projected ``n``-th, so the club projected tenth is 110
    and its neighbours are the same number of places either side.
    """
    return [ranked(100 + place, float(place)) for place in range(1, 21)]


def outcome_row(
    outcome: str,
    n_outcome: int,
    *,
    n_relegated: int = 0,
    n_europe: int = 0,
    n_title: int = 0,
    points_sum: int = 0,
    match_id: int = MATCH_ID,
) -> OutcomeCounts:
    """Build one stored outcome of the next match, with the counts a test writes.

    Every column defaults to zero, so a test names the counts its rule is about
    and the rest stay visibly absent from the assertion.
    """
    return OutcomeCounts(
        match_id=match_id,
        outcome=outcome,
        n_outcome=n_outcome,
        n_relegated=n_relegated,
        n_europe=n_europe,
        n_title=n_title,
        points_sum=points_sum,
    )


def next_match_outcomes() -> list[OutcomeCounts]:
    """Return the three stored outcomes of the ten-run sample the swing tests read.

    Win came out five times and took 15 points, with 3 relegations, 4 European
    places and 1 title; draw three times and 3 points, with 1 relegation and 1
    European place; loss twice and no points, both samples relegated. The counts
    add up to the 10 runs and no conditional count exceeds its outcome, so this
    is one complete run of one match.
    """
    return [
        outcome_row("win", 5, n_relegated=3, n_europe=4, n_title=1, points_sum=15),
        outcome_row("draw", 3, n_relegated=1, n_europe=1, points_sum=3),
        outcome_row("loss", 2, n_relegated=2),
    ]


def test_the_threshold_reads_the_seventeenth_place_of_three_complete_seasons() -> None:
    threshold = survival_threshold(
        [
            stored_season("2007-08", 2007, points_table(35)),
            stored_season("2005-06", 2005, points_table(40)),
            stored_season("2006-07", 2006, points_table(42)),
        ],
        relegation_positions=3,
    )

    # The 17th places are 40, 42 and 35 points, so the counted place is 17, the
    # sample has three seasons, its extremes are 35 and 42, and its middle value
    # is 40: one entry per season, never a second place and never rounded.
    assert threshold == SurvivalThreshold(
        points_median=40.0,
        points_min=35,
        points_max=42,
        seasons_used=3,
        position=17,
    )


def test_an_even_sample_takes_the_mean_of_the_two_middle_totals() -> None:
    threshold = survival_threshold(
        [
            stored_season("2005-06", 2005, points_table(40)),
            stored_season("2006-07", 2006, points_table(42)),
            stored_season("2007-08", 2007, points_table(35)),
            stored_season("2008-09", 2008, points_table(37)),
        ],
        relegation_positions=3,
    )

    # 40, 42, 35 and 37 sort to 35, 37, 40 and 42, so the median is (37 + 40) / 2
    # = 38.5, a half point that is served as it is.
    assert threshold == SurvivalThreshold(
        points_median=38.5,
        points_min=35,
        points_max=42,
        seasons_used=4,
        position=17,
    )


def test_the_configured_relegation_count_moves_the_counted_place() -> None:
    seasons = [
        stored_season("2005-06", 2005, points_table(40, eighteenth=37)),
        stored_season("2006-07", 2006, points_table(42, eighteenth=38)),
        stored_season("2007-08", 2007, points_table(35, eighteenth=33)),
    ]

    # Three relegation places read the 17th: 40, 42 and 35, median 40.
    assert survival_threshold(seasons, relegation_positions=3) == SurvivalThreshold(
        points_median=40.0,
        points_min=35,
        points_max=42,
        seasons_used=3,
        position=17,
    )
    # Two relegation places read the 18th instead, which is a different sample:
    # 37, 38 and 33, median 37 and extremes 33 and 38.
    assert survival_threshold(seasons, relegation_positions=2) == SurvivalThreshold(
        points_median=37.0,
        points_min=33,
        points_max=38,
        seasons_used=3,
        position=18,
    )


def test_a_nineteen_club_season_is_not_counted() -> None:
    complete = stored_season("2005-06", 2005, points_table(40))
    # A 19-club league plays 342 matches, and its 17th place is not the survival
    # line of a 20-club league.
    short_league = stored_season("2006-07", 2006, points_table(38)[:19], results_counted=342)

    threshold = survival_threshold([complete, short_league], relegation_positions=3)

    assert threshold == SurvivalThreshold(
        points_median=40.0,
        points_min=40,
        points_max=40,
        seasons_used=1,
        position=17,
    )


def test_a_season_before_two_thousand_five_six_is_not_counted() -> None:
    complete = stored_season("2005-06", 2005, points_table(40))
    older = stored_season("2004-05", 2004, points_table(44))

    # 2004-05 is a complete 20-club season too, but the stored history starts at
    # 2005-06, so only the later season enters the sample.
    assert survival_threshold([older, complete], relegation_positions=3) == SurvivalThreshold(
        points_median=40.0,
        points_min=40,
        points_max=40,
        seasons_used=1,
        position=17,
    )


def test_a_season_with_an_unplayed_fixture_is_not_counted() -> None:
    complete = stored_season("2005-06", 2005, points_table(40))
    # 379 of the 380 fixtures played: the table is not final, whatever it says.
    unplayed = stored_season("2006-07", 2006, points_table(42), results_counted=379)

    assert survival_threshold([complete, unplayed], relegation_positions=3) == SurvivalThreshold(
        points_median=40.0,
        points_min=40,
        points_max=40,
        seasons_used=1,
        position=17,
    )


def test_a_points_tuple_shorter_than_the_league_is_not_counted() -> None:
    complete = stored_season("2005-06", 2005, points_table(40))
    # 19 totals for a 20-club season: a missing table row, which leaves the
    # season out instead of raising.
    short = stored_season("2006-07", 2006, points_table(42)[:19], n_teams=20)

    assert survival_threshold([complete, short], relegation_positions=3) == SurvivalThreshold(
        points_median=40.0,
        points_min=40,
        points_max=40,
        seasons_used=1,
        position=17,
    )


def test_a_points_tuple_longer_than_the_league_raises() -> None:
    # 20 totals for a 19-club season: the row contradicts its own size, so it is
    # rejected even though the season would never have been counted.
    over_long = stored_season("2005-06", 2005, points_table(40), n_teams=19)

    with pytest.raises(ValueError):
        survival_threshold([over_long], relegation_positions=3)


def test_no_counted_season_means_no_threshold() -> None:
    assert survival_threshold([], relegation_positions=3) is None

    only_excluded = [stored_season("2004-05", 2004, points_table(44))]

    # A season the sample cannot use leaves the threshold unknown, not zero.
    assert survival_threshold(only_excluded, relegation_positions=3) is None


def test_a_relegation_count_outside_the_table_raises() -> None:
    complete = stored_season("2005-06", 2005, points_table(40))

    with pytest.raises(ValueError):
        survival_threshold([complete], relegation_positions=0)
    with pytest.raises(ValueError):
        survival_threshold([complete], relegation_positions=-1)
    # Twenty places of a twenty-club league leave no place for the line itself.
    with pytest.raises(ValueError):
        survival_threshold([complete], relegation_positions=20)


def test_the_race_holds_the_subject_and_the_four_nearest_clubs_either_side() -> None:
    rivals = nearest_rivals(run_of_twenty(), team_id=110)

    # Projected tenth, so the nearest clubs are ninth and eleventh at a distance
    # of 1.0, then eighth and twelfth at 2.0. The tuple holds five clubs in table
    # order, which puts two of the four above the subject and two below it.
    assert rivals == (108, 109, 110, 111, 112)


def test_a_distance_tie_keeps_the_lower_expected_position() -> None:
    # Identifiers run against the projection: the club projected eighth carries
    # the larger id, so only the expected position can separate the two clubs
    # that are both 2.0 away from the subject.
    teams = [ranked(900, 8.0), ranked(110, 10.0), ranked(201, 12.0)]

    assert nearest_rivals(teams, team_id=110, count=1) == (900, 110)
    # No rival is asked for, so only the subject is served.
    assert nearest_rivals(teams, team_id=110, count=0) == (110,)


def test_a_position_tie_keeps_the_lower_identifier() -> None:
    teams = [ranked(500, 10.0), ranked(402, 12.0), ranked(401, 12.0)]

    # Both candidates are 2.0 away from the subject and projected into the same
    # place, so the smaller identifier separates them; the reversed list gives the
    # same answer, because the read order never decides.
    assert nearest_rivals(teams, team_id=500, count=1) == (500, 401)
    assert nearest_rivals(list(reversed(teams)), team_id=500, count=1) == (500, 401)


def test_a_small_field_returns_the_subject_and_every_other_club() -> None:
    teams = [ranked(3, 7.0), ranked(1, 5.0), ranked(2, 6.0)]

    # Three clubs cannot fill four rivals, so the answer is all three of them in
    # table order, and nothing is padded.
    assert nearest_rivals(teams, team_id=2) == (1, 2, 3)


def test_an_absent_or_duplicated_club_and_a_negative_count_raise() -> None:
    teams = run_of_twenty()

    with pytest.raises(ValueError):
        nearest_rivals(teams, team_id=999)
    with pytest.raises(ValueError):
        nearest_rivals([*teams, ranked(110, 10.0)], team_id=110)
    with pytest.raises(ValueError):
        nearest_rivals(teams, team_id=110, count=-1)


def test_the_swing_derives_the_three_outcomes_of_the_stored_run() -> None:
    swings = next_match_swing(next_match_outcomes(), n_runs=10, match_id=MATCH_ID)

    assert swings is not None
    win, draw, loss = swings

    # Five of the ten runs won: half the runs, 3 of those 5 relegated, 4 of them
    # reached Europe, 1 took the title, and they averaged 15 / 5 points.
    assert win.outcome == "win"
    assert win.p_outcome == 0.5
    assert win.p_relegation == 0.6
    assert win.p_europe == 0.8
    assert win.p_title == 0.2
    assert win.expected_points == 3.0

    # Three of the ten runs drew: 1 of those 3 relegated, 1 reached Europe, none
    # took the title, and they averaged 3 / 3 points.
    assert draw.outcome == "draw"
    assert draw.p_outcome == 0.3
    assert draw.p_relegation == pytest.approx(1 / 3)
    assert draw.p_europe == pytest.approx(1 / 3)
    assert draw.p_title == 0.0
    assert draw.expected_points == 1.0

    # Two of the ten runs lost: both were relegated, neither reached Europe or
    # took the title, and they averaged 0 / 2 points.
    assert loss.outcome == "loss"
    assert loss.p_outcome == 0.2
    assert loss.p_relegation == 1.0
    assert loss.p_europe == 0.0
    assert loss.p_title == 0.0
    assert loss.expected_points == 0.0


def test_the_sampled_frequencies_reproduce_the_stored_probabilities() -> None:
    swings = next_match_swing(next_match_outcomes(), n_runs=10, match_id=MATCH_ID)

    assert swings is not None
    # The club's own stored probabilities are 3 + 1 + 2 relegations, 4 + 1 + 0
    # European places and 1 + 0 + 0 titles out of the 10 runs, so weighting each
    # result by how often it happened must give back 0.6, 0.5 and 0.1.
    assert sum(swing.p_outcome * swing.p_relegation for swing in swings) == pytest.approx(
        0.6, abs=1e-9
    )
    assert sum(swing.p_outcome * swing.p_europe for swing in swings) == pytest.approx(0.5, abs=1e-9)
    assert sum(swing.p_outcome * swing.p_title for swing in swings) == pytest.approx(0.1, abs=1e-9)


def test_no_stored_outcome_means_no_swing() -> None:
    # The run has not stored the club's next match, so nothing is known.
    assert next_match_swing([], n_runs=10, match_id=MATCH_ID) is None


def test_a_missing_repeated_or_unnamed_outcome_means_no_swing() -> None:
    rows = next_match_outcomes()

    # Win and draw only: 8 of the 10 runs are accounted for and no swing can be
    # named for the loss, so the answer is unknown rather than a sum error.
    assert next_match_swing(rows[:2], n_runs=10, match_id=MATCH_ID) is None
    # A repeated draw is two rows claiming one result and no loss at all.
    assert next_match_swing([rows[0], rows[1], rows[1]], n_runs=10, match_id=MATCH_ID) is None
    # A fourth result nobody defined is not one of the three the swing reports.
    extra = [rows[0], rows[1], outcome_row("abandoned", 2)]
    assert next_match_swing(extra, n_runs=10, match_id=MATCH_ID) is None


def test_a_stale_match_means_no_swing() -> None:
    # The rows describe match 500 and the club's next match is 501, so the sample
    # belongs to a match already played as of the day and stays unknown until the
    # next run replaces it -- even though the rows add up to the 10 runs.
    assert next_match_swing(next_match_outcomes(), n_runs=10, match_id=501) is None


def test_a_never_sampled_outcome_means_no_swing() -> None:
    rows = [
        outcome_row("win", 5, n_relegated=3, points_sum=15),
        outcome_row("draw", 3, n_relegated=1, points_sum=3),
        outcome_row("loss", 0),
    ]

    # 5 + 3 + 0 are exactly the 8 runs, so the rows are complete; the loss was
    # simply never sampled, and dividing by its zero count would invent a
    # conditional probability, so the never-sampled outcome wins over the sum.
    assert next_match_swing(rows, n_runs=8, match_id=MATCH_ID) is None


def test_rows_of_two_matches_raise() -> None:
    rows = next_match_outcomes()
    rows[2] = outcome_row("loss", 2, n_relegated=2, match_id=MATCH_ID + 1)

    # A club has one next match, so rows of two matches are not this club's swing.
    with pytest.raises(ValueError):
        next_match_swing(rows, n_runs=10, match_id=MATCH_ID)


def test_a_conditional_count_above_its_outcome_raises() -> None:
    rows = next_match_outcomes()
    # 6 relegations in 5 winning runs: a conditional probability of 1.2, which no
    # run can produce, so the broken row is raised instead of served as unknown.
    rows[0] = outcome_row("win", 5, n_relegated=6, points_sum=15)

    with pytest.raises(ValueError):
        next_match_swing(rows, n_runs=10, match_id=MATCH_ID)


def test_outcomes_that_do_not_add_up_to_the_runs_raise() -> None:
    rows = next_match_outcomes()
    # 5 + 3 + 3 outcomes are 11 samples of a 10-run simulation.
    rows[2] = outcome_row("loss", 3, n_relegated=2)

    with pytest.raises(ValueError):
        next_match_swing(rows, n_runs=10, match_id=MATCH_ID)


def test_an_undefined_run_count_raises() -> None:
    rows = next_match_outcomes()

    # A frequency over zero runs, or over a negative number of them, is not a
    # probability the module may report.
    with pytest.raises(ValueError):
        next_match_swing(rows, n_runs=0, match_id=MATCH_ID)
    with pytest.raises(ValueError):
        next_match_swing(rows, n_runs=-10, match_id=MATCH_ID)
