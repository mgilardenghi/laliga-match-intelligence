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
    Qualification,
    SeasonSimulation,
    SimFixture,
    TeamSimulation,
    engine,
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
