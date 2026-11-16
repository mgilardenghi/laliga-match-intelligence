"""Tests for :func:`lmi_core.simulation.simulate_season`.

All data is synthetic: integer team ids, invented names such as ``"Sim Town
Alpha"``, and hand-built scoreline matrices. No real club, league or odds data
appears here. Expected tables come from
:func:`lmi_core.standings.compute_standings` over the exact results, which is the
ranking the engine itself reuses, so a failing assertion points at the sampling,
the bookkeeping or the report rather than at the tie-break chain.
"""

import dataclasses
import datetime
from collections.abc import Mapping, Sequence

import numpy as np
import pytest
from lmi_core.simulation import (
    DEFAULT_SEED,
    Outcome,
    Qualification,
    SeasonSimulation,
    SimFixture,
    TeamSimulation,
    engine,
    fixture_outcome_probs,
    simulate_season,
)
from lmi_core.standings import MatchResult, compute_standings
from numpy.typing import NDArray

ALPHA = 1
BRAVO = 2
CHARLIE = 3
DELTA = 4

TEAMS = {
    ALPHA: "Sim Town Alpha",
    BRAVO: "Sim Town Bravo",
    CHARLIE: "Sim Town Charlie",
    DELTA: "Sim Town Delta",
}

QUALIFICATION = Qualification(ucl_positions=2, europe_positions=3, relegation_positions=1)
"""Two continental places, three European places and one relegation place in four teams."""

OUTCOMES: tuple[Outcome, ...] = ("win", "draw", "loss")
"""The three outcomes, in the order the engine reports them."""

_START = datetime.date(2026, 1, 1)


def virtual_fixture(match_id: int, home: int, away: int, day_offset: int = 0) -> SimFixture:
    """Build a virtual fixture played ``day_offset`` days after 2026-01-01."""
    return SimFixture(
        match_id=match_id,
        home_team_id=home,
        away_team_id=away,
        sim_date=_START + datetime.timedelta(days=day_offset),
    )


def played(
    home: int, away: int, home_goals: int, away_goals: int, day_offset: int = 0
) -> MatchResult:
    """Build a synthetic result played ``day_offset`` days after 2026-01-01."""
    return MatchResult(
        home_team_id=home,
        away_team_id=away,
        home_goals=home_goals,
        away_goals=away_goals,
        kickoff_date=_START + datetime.timedelta(days=day_offset),
    )


def certain_scorelines(*scorelines: tuple[int, int]) -> NDArray[np.float64]:
    """Return one certain scoreline matrix per scoreline, in the given order."""
    probs = np.zeros((len(scorelines), 11, 11), dtype=np.float64)
    for index, (home_goals, away_goals) in enumerate(scorelines):
        probs[index, home_goals, away_goals] = 1.0
    return probs


def flat_scorelines(n_fixtures: int) -> NDArray[np.float64]:
    """Return ``n_fixtures`` flat grids: every one of the 121 scorelines is as likely."""
    return np.full((n_fixtures, 11, 11), 1 / 121, dtype=np.float64)


def double_round_robin(team_ids: Sequence[int]) -> list[SimFixture]:
    """Return every ordered pair of distinct teams as one fixture.

    Each pair therefore meets at home and away. Match ids follow the list order
    and so do the dates, so the fixtures are already in ``(sim_date, match_id)``
    order, which is the order the engine plays them in.
    """
    fixtures: list[SimFixture] = []
    for home in team_ids:
        for away in team_ids:
            if home != away:
                fixtures.append(
                    virtual_fixture(len(fixtures) + 1, home, away, day_offset=len(fixtures))
                )
    return fixtures


ROUND_ROBIN = tuple(double_round_robin((ALPHA, BRAVO, CHARLIE, DELTA)))
"""The 12 fixtures of a four-team double round robin."""


def by_id(simulation: SeasonSimulation) -> dict[int, TeamSimulation]:
    """Return the per-team rows keyed by team id, ignoring the reported order."""
    return {team.team_id: team for team in simulation.teams}


def run_engine(
    *,
    teams: Mapping[int, str] = TEAMS,
    fixed: Sequence[MatchResult] = (),
    fixtures: Sequence[SimFixture] = ROUND_ROBIN,
    score_probs: NDArray[np.float64] | None = None,
    n_runs: int = 20,
    seed: int = 7,
    qualification: Qualification = QUALIFICATION,
) -> SeasonSimulation:
    """Call the engine with valid defaults, so a test varies only what it checks."""
    matrix = flat_scorelines(len(fixtures)) if score_probs is None else score_probs
    return simulate_season(
        teams,
        fixed,
        fixtures,
        matrix,
        n_runs=n_runs,
        seed=seed,
        qualification=qualification,
    )


def test_the_same_seed_reproduces_the_same_rows() -> None:
    # The caller's guarantee: same data, same as-of split, same run count and
    # same seed give identical rows, bit for bit.
    first = run_engine(n_runs=200, seed=DEFAULT_SEED)
    second = run_engine(n_runs=200, seed=DEFAULT_SEED)

    assert [team.team_id for team in first.teams] == [team.team_id for team in second.teams]
    for left, right in zip(first.teams, second.teams, strict=True):
        assert left.mean_points == right.mean_points
        assert np.array_equal(left.position_probs, right.position_probs)
        assert left.p_title == right.p_title

    # A different seed has to change the draws; otherwise the seed is dead weight.
    reproduced = by_id(first)
    assert any(
        not np.array_equal(reproduced[team_id].position_probs, row.position_probs)
        for team_id, row in by_id(run_engine(n_runs=200, seed=DEFAULT_SEED + 1)).items()
    )


def test_a_degenerate_season_reproduces_the_exact_table() -> None:
    # Every matrix is P(1-0) = 1, so every fixture is a certain home win, every
    # run is the same season, and the report must be the table that
    # compute_standings builds from those exact results. Nine points each and
    # level goals leave the name to order the table.
    fixtures = list(ROUND_ROBIN)
    exact = [
        MatchResult(
            home_team_id=entry.home_team_id,
            away_team_id=entry.away_team_id,
            home_goals=1,
            away_goals=0,
            kickoff_date=entry.sim_date,
        )
        for entry in fixtures
    ]
    expected = compute_standings(TEAMS, exact)
    assert {row.points for row in expected} == {9}

    simulation = run_engine(
        fixtures=fixtures,
        score_probs=certain_scorelines(*[(1, 0)] * len(fixtures)),
        n_runs=50,
    )
    rows = by_id(simulation)

    assert [team.team_id for team in simulation.teams] == [row.team_id for row in expected]
    for row in expected:
        team = rows[row.team_id]
        assert team.mean_points == row.points
        assert (team.points_min, team.points_p05, team.points_p95) == (row.points,) * 3
        assert team.points_pmf.tolist() == [1.0]
        assert team.mean_position == row.position
        assert team.position_probs[row.position - 1] == 1.0
        assert np.count_nonzero(team.position_probs) == 1
        assert team.position_probs.sum() == pytest.approx(1.0, abs=1e-12)
    assert rows[ALPHA].p_title == 1.0
    assert rows[ALPHA].p_ucl == 1.0
    assert rows[DELTA].p_europe == 0.0
    assert rows[DELTA].p_relegation == 1.0


def test_each_probability_row_travels_with_its_fixture() -> None:
    # The caller's order is not the engine's: the fixtures arrive shuffled and
    # every one of them has a different certain scoreline, so a probability row
    # that stops following its fixture changes the table.
    fixtures = [
        virtual_fixture(2, BRAVO, CHARLIE, day_offset=7),
        virtual_fixture(1, ALPHA, BRAVO, day_offset=0),
        virtual_fixture(4, CHARLIE, DELTA, day_offset=7),
        virtual_fixture(3, DELTA, ALPHA, day_offset=0),
    ]
    score_probs = certain_scorelines((2, 1), (0, 3), (4, 0), (1, 1))
    # In (sim_date, match_id) order those rows mean: Alpha 0-3 Bravo, Delta 1-1
    # Alpha, Bravo 2-1 Charlie, Charlie 4-0 Delta. Bravo take 6 points, Charlie
    # 3, and Alpha and Delta 1 each with Alpha ahead on goal difference.
    expected_points = {BRAVO: 6, CHARLIE: 3, ALPHA: 1, DELTA: 1}

    simulation = run_engine(fixtures=fixtures, score_probs=score_probs, n_runs=5)
    rows = by_id(simulation)

    assert simulation.n_fixtures == 4
    for team_id, points in expected_points.items():
        assert rows[team_id].mean_points == points
    assert [team.team_id for team in simulation.teams] == [BRAVO, CHARLIE, ALPHA, DELTA]


def test_with_no_simulated_fixtures_the_report_is_the_exact_table() -> None:
    fixed = [
        played(ALPHA, BRAVO, 2, 0),
        played(CHARLIE, DELTA, 1, 1),
        played(BRAVO, CHARLIE, 0, 3, day_offset=7),
    ]
    expected = compute_standings(TEAMS, fixed)

    simulation = run_engine(
        fixed=fixed,
        fixtures=[],
        score_probs=np.zeros((0, 11, 11), dtype=np.float64),
        n_runs=25,
    )

    assert simulation.n_fixtures == 0
    assert [team.team_id for team in simulation.teams] == [row.team_id for row in expected]
    rows = by_id(simulation)
    for row in expected:
        team = rows[row.team_id]
        assert team.mean_points == row.points
        assert team.points_min == row.points
        assert team.points_pmf.tolist() == [1.0]
        assert team.mean_position == row.position
        assert team.position_probs[row.position - 1] == 1.0
        assert np.count_nonzero(team.position_probs) == 1
        assert team.p_title == (1.0 if row.position == 1 else 0.0)
        assert team.p_ucl == (1.0 if row.position <= QUALIFICATION.ucl_positions else 0.0)
        assert team.p_europe == (1.0 if row.position <= QUALIFICATION.europe_positions else 0.0)
        assert team.p_relegation == (
            1.0 if row.position > len(TEAMS) - QUALIFICATION.relegation_positions else 0.0
        )


def test_two_symmetric_teams_split_the_title_evenly() -> None:
    """Two evenly matched teams must each win the title half the time.

    Every fixture is a certain 1-0 home win or 0-1 away win, half the time each,
    with the same matrix for both teams, so a draw is impossible and the venue
    gives nobody an edge. An odd number of fixtures keeps the points from ever
    being level, because each fixture hands out exactly three points to exactly
    one team; that matters, since a level table would fall through to the team
    name and the estimate would stop being "one half each".

    With ``n_runs`` draws the estimate is a binomial proportion with standard
    deviation ``sigma = 0.5 / sqrt(n_runs)``. The assertion allows four of those:
    wide enough that sampling noise does not break the suite, narrow enough that
    a real home-venue or pairing bias fails it.
    """
    teams = {ALPHA: "Sim Town Alpha", BRAVO: "Sim Town Bravo"}
    fixtures = [
        virtual_fixture(1, ALPHA, BRAVO, day_offset=0),
        virtual_fixture(2, BRAVO, ALPHA, day_offset=7),
        virtual_fixture(3, ALPHA, BRAVO, day_offset=14),
    ]
    coin = np.zeros((11, 11), dtype=np.float64)
    coin[1, 0] = 0.5
    coin[0, 1] = 0.5
    n_runs = 10_000

    simulation = run_engine(
        teams=teams,
        fixtures=fixtures,
        score_probs=np.stack([coin, coin, coin]),
        n_runs=n_runs,
        qualification=Qualification(ucl_positions=1, europe_positions=1, relegation_positions=1),
    )

    sigma = 0.5 / np.sqrt(n_runs)
    assert len(simulation.teams) == 2
    for team in simulation.teams:
        assert abs(team.p_title - 0.5) <= 4 * sigma
        # In a two-team league the continental place and the relegation place
        # partition the table, so both probabilities are the title's complement.
        assert team.p_ucl == team.p_title
        assert team.p_relegation == pytest.approx(1.0 - team.p_title)


def test_the_report_keeps_its_shape_and_ordering_invariants() -> None:
    simulation = run_engine(n_runs=300, seed=13)
    n_teams = len(TEAMS)
    positions = np.arange(1, n_teams + 1)

    assert simulation.n_runs == 300
    assert simulation.seed == 13
    assert simulation.n_fixtures == len(ROUND_ROBIN)
    assert len(simulation.teams) == n_teams
    for team in simulation.teams:
        assert team.team_id in TEAMS
        assert isinstance(team.points_min, int)
        assert team.points_pmf.sum() == pytest.approx(1.0, abs=1e-12)
        assert np.all(team.points_pmf >= 0)
        assert team.position_probs.shape == (n_teams,)
        assert team.position_probs.sum() == pytest.approx(1.0, abs=1e-12)
        assert team.mean_position == pytest.approx(float(team.position_probs @ positions))
        assert team.p_title == team.position_probs[0]
        assert team.p_ucl == pytest.approx(team.position_probs[: QUALIFICATION.ucl_positions].sum())
        assert team.p_europe == pytest.approx(
            team.position_probs[: QUALIFICATION.europe_positions].sum()
        )
        assert team.p_relegation == pytest.approx(
            team.position_probs[n_teams - QUALIFICATION.relegation_positions :].sum()
        )

        highest_total = team.points_min + len(team.points_pmf) - 1
        quantiles = [
            team.points_p05,
            team.points_p25,
            team.points_p50,
            team.points_p75,
            team.points_p95,
        ]
        assert quantiles == sorted(quantiles)
        assert team.points_min <= quantiles[0]
        assert quantiles[-1] <= highest_total
        assert team.points_min <= team.mean_points <= highest_total

    # The projected table is the documented key: mean position, then mean points
    # descending, then team name, then team id.
    keys = [
        (team.mean_position, -team.mean_points, TEAMS[team.team_id], team.team_id)
        for team in simulation.teams
    ]
    assert keys == sorted(keys)


def test_a_qualification_probability_never_exceeds_one() -> None:
    """A place group's probability is the integer count divided once.

    The counts are the ones the Compose verification produced for one real
    team: six of its 2345 simulated seasons finished in the top six. Adding the
    six already divided place probabilities gave ``1.0000000000000002``, which
    is not a probability and which the stored CHECK rejects, so that backfill
    run failed. One division of an integer sum is exactly ``1.0``.
    """
    n_runs = 2345
    n_teams = 8
    counts = [475, 1478, 317, 74, 0, 1, 0, 0]
    assert sum(counts) == n_runs
    places = np.repeat(np.arange(1, n_teams + 1, dtype=np.int64), counts)

    team = engine._team_simulation(
        ALPHA,
        points=np.zeros(n_runs, dtype=np.int64),
        places=places,
        n_runs=n_runs,
        n_teams=n_teams,
        qualification=Qualification(),
    )

    assert team.p_europe == 1.0
    assert team.p_ucl == 2344 / n_runs
    assert team.p_title == 475 / n_runs
    assert 0.0 <= team.p_title <= 1.0
    assert 0.0 <= team.p_ucl <= 1.0
    assert 0.0 <= team.p_europe <= 1.0
    assert 0.0 <= team.p_relegation <= 1.0
    assert team.position_probs.sum() == pytest.approx(1.0, abs=1e-12)


def test_a_twenty_team_double_round_robin_reports_every_team() -> None:
    # The league size the caller uses, with a run count small enough to keep the
    # suite quick: it proves the engine handles the real shape end to end.
    team_ids = tuple(range(101, 121))
    teams = {team_id: f"Sim Town {team_id:02d}" for team_id in team_ids}
    fixtures = double_round_robin(team_ids)

    simulation = run_engine(
        teams=teams,
        fixtures=fixtures,
        n_runs=200,
        qualification=Qualification(),
    )

    assert simulation.n_fixtures == 380
    assert len(simulation.teams) == 20
    assert {team.team_id for team in simulation.teams} == set(team_ids)
    for team in simulation.teams:
        assert team.position_probs.shape == (20,)
        assert team.position_probs.sum() == pytest.approx(1.0, abs=1e-12)
        assert team.points_pmf.sum() == pytest.approx(1.0, abs=1e-12)


def test_the_public_dataclasses_keep_the_specified_shape() -> None:
    # The service and the CLI read these names, so the field names and their
    # order are the contract, not an implementation detail.
    assert [field.name for field in dataclasses.fields(Qualification)] == [
        "ucl_positions",
        "europe_positions",
        "relegation_positions",
    ]
    assert [field.name for field in dataclasses.fields(SimFixture)] == [
        "match_id",
        "home_team_id",
        "away_team_id",
        "sim_date",
    ]
    assert [field.name for field in dataclasses.fields(TeamSimulation)] == [
        "team_id",
        "mean_points",
        "points_min",
        "points_p05",
        "points_p25",
        "points_p50",
        "points_p75",
        "points_p95",
        "points_pmf",
        "mean_position",
        "position_probs",
        "p_title",
        "p_ucl",
        "p_europe",
        "p_relegation",
    ]
    assert [field.name for field in dataclasses.fields(SeasonSimulation)] == [
        "n_runs",
        "seed",
        "n_fixtures",
        "teams",
        "next_match",
    ]
    assert Qualification() == Qualification(
        ucl_positions=4, europe_positions=6, relegation_positions=3
    )
    assert DEFAULT_SEED == 0

    team = run_engine(n_runs=3).teams[0]
    fixture_entry = virtual_fixture(1, ALPHA, BRAVO)
    for instance, attribute in ((team, "team_id"), (fixture_entry, "match_id")):
        assert not hasattr(instance, "__dict__")
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(instance, attribute, 0)


def test_the_draw_count_and_the_score_matrix_are_validated() -> None:
    with pytest.raises(ValueError, match="n_runs must be at least 1"):
        run_engine(n_runs=0)

    with pytest.raises(ValueError, match=r"score_probs must have shape \(12, 11, 11\)"):
        run_engine(score_probs=np.full((11, 11), 1 / 121))

    negative = flat_scorelines(len(ROUND_ROBIN))
    negative[3, 0, 0] = -0.5
    negative[3, 0, 1] += 0.5
    with pytest.raises(ValueError, match="score_probs row 3 has a negative probability"):
        run_engine(score_probs=negative)

    # A NaN row passes the negativity and row-total checks, because every
    # comparison against NaN is false, so it must be rejected on its own.
    nan_cell = flat_scorelines(len(ROUND_ROBIN))
    nan_cell[7, 2, 3] = np.nan
    with pytest.raises(ValueError, match="score_probs row 7 has a non-finite probability"):
        run_engine(score_probs=nan_cell)

    infinite_cell = flat_scorelines(len(ROUND_ROBIN))
    infinite_cell[6, 1, 1] = np.inf
    with pytest.raises(ValueError, match="score_probs row 6 has a non-finite probability"):
        run_engine(score_probs=infinite_cell)

    off_total = flat_scorelines(len(ROUND_ROBIN))
    off_total[5, 4, 4] += 0.5
    with pytest.raises(ValueError, match="score_probs row 5 sums to"):
        run_engine(score_probs=off_total)


def test_the_league_and_its_qualification_regions_are_validated() -> None:
    with pytest.raises(ValueError, match="teams must not be empty"):
        run_engine(teams={})
    with pytest.raises(ValueError, match="europe_positions < n_teams"):
        run_engine(
            qualification=Qualification(ucl_positions=2, europe_positions=4, relegation_positions=1)
        )
    with pytest.raises(ValueError, match="1 <= ucl_positions"):
        run_engine(
            qualification=Qualification(ucl_positions=0, europe_positions=3, relegation_positions=1)
        )
    with pytest.raises(ValueError, match="relegation_positions < n_teams"):
        run_engine(
            qualification=Qualification(ucl_positions=2, europe_positions=3, relegation_positions=4)
        )
    with pytest.raises(ValueError, match="1 <= relegation_positions"):
        run_engine(
            qualification=Qualification(ucl_positions=2, europe_positions=3, relegation_positions=0)
        )


def test_a_fixture_naming_an_unknown_team_is_validated() -> None:
    fixtures = [*ROUND_ROBIN, virtual_fixture(99, ALPHA, 999)]

    with pytest.raises(ValueError, match="fixture 99 names team 999, which is not in teams"):
        run_engine(fixtures=fixtures)


def run_conditioned(
    condition_on: Mapping[int, int],
    *,
    teams: Mapping[int, str] = TEAMS,
    fixtures: Sequence[SimFixture] = ROUND_ROBIN,
    score_probs: NDArray[np.float64] | None = None,
    n_runs: int = 20,
    seed: int = 7,
    qualification: Qualification = QUALIFICATION,
) -> SeasonSimulation:
    """Mirror :func:`run_engine` with a next-match map and no fixed results."""
    matrix = flat_scorelines(len(fixtures)) if score_probs is None else score_probs
    return simulate_season(
        teams,
        (),
        fixtures,
        matrix,
        n_runs=n_runs,
        seed=seed,
        qualification=qualification,
        condition_on=condition_on,
    )


def all_teams_next_fixture(
    fixtures: Sequence[SimFixture], team_ids: Sequence[int]
) -> dict[int, int]:
    """Map every team to the earliest fixture it plays, at home or away."""
    return {
        team_id: next(
            fixture.match_id
            for fixture in fixtures
            if team_id in (fixture.home_team_id, fixture.away_team_id)
        )
        for team_id in team_ids
    }


def recount_conditioned(
    *,
    teams: Mapping[int, str],
    fixtures: Sequence[SimFixture],
    score_probs: NDArray[np.float64],
    n_runs: int,
    seed: int,
    qualification: Qualification,
    condition_on: Mapping[int, int],
) -> dict[tuple[int, Outcome], tuple[int, int, int, int, int]]:
    """Recount the next-match counts with a direct simulation of the test's own.

    The engine documents its draw: one ``PCG64`` stream per seed, one uniform per
    ``(run, fixture)`` pair in the order the fixtures are played, and the
    scoreline as the inverse of that fixture's cumulative distribution. Replaying
    exactly that stream is what makes the two counts comparable, but everything
    after the draw is the test's own: it ranks with
    :func:`lmi_core.standings.compute_standings` over results it builds itself and
    counts the outcomes and the finishes by hand, with no engine array or helper
    involved. ``fixtures`` must therefore already be in ``(sim_date, match_id)``
    order, which is the order the engine plays them in.

    The value per key is ``(n_outcome, n_relegated, n_europe, n_title,
    points_sum)``, matching the fields of one reported row.
    """
    goals = 11
    cells = goals * goals
    grids = np.asarray(score_probs, dtype=np.float64).reshape(len(fixtures), cells)
    cumulative = np.cumsum(grids, axis=1)
    uniforms = np.random.Generator(np.random.PCG64(seed)).random((n_runs, len(fixtures)))
    relegation_from = len(teams) - qualification.relegation_positions
    tallies: dict[tuple[int, Outcome], list[int]] = {
        (team_id, outcome): [0, 0, 0, 0, 0] for team_id in teams for outcome in OUTCOMES
    }
    for run in range(n_runs):
        results: list[MatchResult] = []
        sampled: dict[int, tuple[int, int]] = {}
        for index, fixture in enumerate(fixtures):
            cell = int(np.searchsorted(cumulative[index], uniforms[run, index], side="right"))
            cell = min(cell, cells - 1)
            home_goals, away_goals = divmod(cell, goals)
            sampled[fixture.match_id] = (home_goals, away_goals)
            results.append(
                MatchResult(
                    home_team_id=fixture.home_team_id,
                    away_team_id=fixture.away_team_id,
                    home_goals=home_goals,
                    away_goals=away_goals,
                    kickoff_date=fixture.sim_date,
                )
            )
        table = {row.team_id: row for row in compute_standings(teams, results)}
        for team_id, match_id in condition_on.items():
            fixture = next(entry for entry in fixtures if entry.match_id == match_id)
            home_goals, away_goals = sampled[match_id]
            outcome: Outcome
            if home_goals == away_goals:
                outcome = "draw"
            elif (home_goals > away_goals) == (fixture.home_team_id == team_id):
                outcome = "win"
            else:
                outcome = "loss"
            row = table[team_id]
            tally = tallies[(team_id, outcome)]
            tally[0] += 1
            tally[1] += int(row.position > relegation_from)
            tally[2] += int(row.position <= qualification.europe_positions)
            tally[3] += int(row.position == 1)
            tally[4] += row.points
    counts: dict[tuple[int, Outcome], tuple[int, int, int, int, int]] = {}
    for (team_id, outcome), tally in tallies.items():
        counts[(team_id, outcome)] = (tally[0], tally[1], tally[2], tally[3], tally[4])
    return counts


def test_a_conditioned_call_reproduces_the_plain_rows_bit_for_bit() -> None:
    # Asking how the next fixture moves the season must not move a single draw:
    # the counting reads arrays the runs already produced, so every field of every
    # team row, including the arrays, is identical to an unconditioned call.
    condition_on = all_teams_next_fixture(ROUND_ROBIN, tuple(TEAMS))
    plain = run_engine(n_runs=200, seed=11)
    conditioned = run_conditioned(condition_on, n_runs=200, seed=11)

    assert plain.next_match == ()
    assert (plain.n_runs, plain.seed, plain.n_fixtures) == (
        conditioned.n_runs,
        conditioned.seed,
        conditioned.n_fixtures,
    )
    assert [team.team_id for team in plain.teams] == [team.team_id for team in conditioned.teams]
    for left, right in zip(plain.teams, conditioned.teams, strict=True):
        for field in dataclasses.fields(TeamSimulation):
            left_value = getattr(left, field.name)
            right_value = getattr(right, field.name)
            if isinstance(left_value, np.ndarray):
                assert np.array_equal(left_value, right_value), field.name
            else:
                assert left_value == right_value, field.name


def test_each_outcome_row_reconciles_with_its_team_row() -> None:
    # Every run lands in exactly one outcome cell, and the finish cells of the
    # three cells are the runs the team row counted for that region. The points
    # totals add up to the mean the team row reports, times the run count.
    n_runs = 400
    condition_on = all_teams_next_fixture(ROUND_ROBIN, tuple(TEAMS))
    simulation = run_conditioned(condition_on, n_runs=n_runs, seed=5)
    rows = by_id(simulation)

    assert len(simulation.next_match) == len(TEAMS) * len(OUTCOMES)
    for team_id in TEAMS:
        team = rows[team_id]
        counts = [row for row in simulation.next_match if row.team_id == team_id]
        assert [row.outcome for row in counts] == list(OUTCOMES)
        assert {row.match_id for row in counts} == {condition_on[team_id]}
        assert sum(row.n_outcome for row in counts) == n_runs
        assert sum(row.n_relegated for row in counts) == round(team.p_relegation * n_runs)
        assert sum(row.n_europe for row in counts) == round(team.p_europe * n_runs)
        assert sum(row.n_title for row in counts) == round(team.p_title * n_runs)
        assert sum(row.points_sum for row in counts) == pytest.approx(
            team.mean_points * n_runs, abs=1e-6
        )
    # The reported order is the contract: by team id, then win, draw, loss.
    assert [(row.team_id, row.outcome) for row in simulation.next_match] == [
        (team_id, outcome) for team_id in sorted(TEAMS) for outcome in OUTCOMES
    ]


def test_the_next_match_counts_equal_a_brute_force_recount() -> None:
    # Six teams and 200 runs, with half the league conditioned on a home fixture
    # and half on an away one, so both sides of the win/draw/loss split are
    # recounted. The recount replays the same draws but counts everything itself,
    # so a sign error, a swapped side or a misread region shows up as a difference.
    team_ids = tuple(range(201, 207))
    teams = {team_id: f"Sim Town {team_id}" for team_id in team_ids}
    fixtures = double_round_robin(team_ids)
    qualification = Qualification(ucl_positions=2, europe_positions=3, relegation_positions=1)
    score_probs = flat_scorelines(len(fixtures))
    n_runs = 200
    seed = 23
    condition_on: dict[int, int] = {}
    for team_id in team_ids:
        home_fixture = next(fixture for fixture in fixtures if fixture.home_team_id == team_id)
        away_fixture = next(fixture for fixture in fixtures if fixture.away_team_id == team_id)
        condition_on[team_id] = (home_fixture if team_id % 2 else away_fixture).match_id

    simulation = simulate_season(
        teams,
        (),
        fixtures,
        score_probs,
        n_runs=n_runs,
        seed=seed,
        qualification=qualification,
        condition_on=condition_on,
    )
    expected = recount_conditioned(
        teams=teams,
        fixtures=fixtures,
        score_probs=score_probs,
        n_runs=n_runs,
        seed=seed,
        qualification=qualification,
        condition_on=condition_on,
    )
    reported = {(row.team_id, row.outcome): row for row in simulation.next_match}

    assert set(reported) == set(expected)
    for key, (n_outcome, n_relegated, n_europe, n_title, points_sum) in expected.items():
        row = reported[key]
        assert row.match_id == condition_on[row.team_id]
        assert (row.n_outcome, row.n_relegated, row.n_europe, row.n_title, row.points_sum) == (
            n_outcome,
            n_relegated,
            n_europe,
            n_title,
            points_sum,
        )
    # A recount of all zeros would agree with an engine that never counted, so
    # every outcome has to have happened at least once.
    assert all(row.n_outcome > 0 for row in simulation.next_match)


def test_a_degenerate_season_puts_every_run_in_one_outcome_cell() -> None:
    # Every grid is P(1-0) = 1, so every run is the same season: the home side of
    # the conditioned fixture wins every run and the away side loses every run,
    # with the finish cells taken from that one exact table.
    n_runs = 50
    fixtures = list(ROUND_ROBIN)
    alpha_home = next(fixture for fixture in fixtures if fixture.home_team_id == ALPHA)
    bravo_away = next(
        fixture
        for fixture in fixtures
        if fixture.home_team_id == ALPHA and fixture.away_team_id == BRAVO
    )
    simulation = run_conditioned(
        {ALPHA: alpha_home.match_id, BRAVO: bravo_away.match_id},
        fixtures=fixtures,
        score_probs=certain_scorelines(*[(1, 0)] * len(fixtures)),
        n_runs=n_runs,
    )
    rows = {(row.team_id, row.outcome): row for row in simulation.next_match}

    assert rows[(ALPHA, "win")].n_outcome == n_runs
    assert rows[(ALPHA, "draw")].n_outcome == 0
    assert rows[(ALPHA, "loss")].n_outcome == 0
    # Alpha top the degenerate table on name, with nine points from three home
    # wins, three European places and no relegation place in a four-team league.
    assert rows[(ALPHA, "win")].n_title == n_runs
    assert rows[(ALPHA, "win")].n_europe == n_runs
    assert rows[(ALPHA, "win")].n_relegated == 0
    assert rows[(ALPHA, "win")].points_sum == 9 * n_runs
    assert rows[(ALPHA, "draw")].points_sum == 0
    # Bravo lose that fixture every run and finish second.
    assert rows[(BRAVO, "loss")].n_outcome == n_runs
    assert rows[(BRAVO, "win")].n_outcome == 0
    assert rows[(BRAVO, "loss")].n_title == 0
    assert rows[(BRAVO, "loss")].n_europe == n_runs
    assert rows[(BRAVO, "loss")].n_relegated == 0
    assert rows[(BRAVO, "loss")].points_sum == 9 * n_runs


def test_a_conditioned_next_match_is_validated() -> None:
    with pytest.raises(ValueError, match="condition_on names match 999"):
        run_conditioned({ALPHA: 999})

    # A club can only be conditioned on a fixture it plays: Charlie host Bravo.
    other_fixture = next(
        fixture
        for fixture in ROUND_ROBIN
        if fixture.home_team_id == CHARLIE and fixture.away_team_id == BRAVO
    )
    with pytest.raises(
        ValueError,
        match=f"condition_on maps team {ALPHA} to match {other_fixture.match_id}",
    ):
        run_conditioned({ALPHA: other_fixture.match_id})


def test_fixture_outcome_probs_reads_the_grids_without_a_draw() -> None:
    # Two fixtures, cells chosen by hand so the expected sums are readable. The
    # second row totals 5e-10 above one, which the engine accepts, so its three
    # probabilities are normalized by that total rather than by one.
    fixtures = [
        virtual_fixture(41, ALPHA, BRAVO, day_offset=0),
        virtual_fixture(42, BRAVO, ALPHA, day_offset=7),
    ]
    probs = np.zeros((2, 11, 11), dtype=np.float64)
    probs[0, 2, 0] = 0.2
    probs[0, 1, 1] = 0.3
    probs[0, 5, 4] = 0.4
    probs[0, 0, 2] = 0.1
    probs[1, 3, 3] = 0.25
    probs[1, 0, 0] = 0.25
    probs[1, 0, 1] = 0.25
    probs[1, 1, 0] = 0.2500000005

    reported = fixture_outcome_probs(fixtures, probs)

    assert [entry.match_id for entry in reported] == [41, 42]
    # First row: two home wins (2-0 and 5-4), a draw and an away win. Second row:
    # one home win, two draws and one away win, with the home win carrying the
    # extra 5e-10.
    expected = [(0.6, 0.3, 0.1), (0.2500000005, 0.5, 0.25)]
    for entry, (p_home, p_draw, p_away) in zip(reported, expected, strict=True):
        for value in (entry.p_home, entry.p_draw, entry.p_away):
            assert 0.0 <= value <= 1.0
        assert entry.p_home + entry.p_draw + entry.p_away == pytest.approx(1.0, abs=1e-12)
        assert entry.p_home == pytest.approx(p_home, abs=1e-9)
        assert entry.p_draw == pytest.approx(p_draw, abs=1e-9)
        assert entry.p_away == pytest.approx(p_away, abs=1e-9)


def test_fixture_outcome_probs_validates_the_grids() -> None:
    with pytest.raises(ValueError, match=r"score_probs must have shape \(2, 11, 11\)"):
        fixture_outcome_probs(
            [virtual_fixture(1, ALPHA, BRAVO), virtual_fixture(2, BRAVO, ALPHA)],
            flat_scorelines(3),
        )
