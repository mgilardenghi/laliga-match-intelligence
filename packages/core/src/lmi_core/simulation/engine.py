"""Monte Carlo season simulation: pure, seeded and free of any I/O.

:func:`simulate_season` is the core the M4 season simulation calls. It takes the
results already played, the fixtures still to play with one scoreline
distribution each, and a seed, and returns per-team distributions of final points
and table positions. No season, matchday, clock or database concept enters here:
an as-of date is the caller's business, expressed as the split between ``fixed``
and ``fixtures``.

Two properties explain the shape of the code:

- Reproducibility. Sampling draws from one :class:`numpy.random.PCG64` stream
  seeded by the caller and never from the global generator, so the same
  arguments always give identical rows.
- One ranking implementation. Every run is ranked by
  :func:`lmi_core.standings.compute_standings`, so a simulated table cannot drift
  from the real one; a second ranking here would be a second set of tie-break
  rules waiting to disagree.

The scoreline grid is fixed at eleven goals per side: ``score_probs[i, h, a]`` is
the probability that fixture ``i`` ends ``h``-``a``, with ``h`` and ``a`` in
``0..10``.
"""

import datetime
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from lmi_core.standings import MatchResult, compute_standings

_SCORELINE_GOALS: Final = 11
"""Goals covered per side: ``0..10``."""

_SCORELINE_CELLS: Final = _SCORELINE_GOALS * _SCORELINE_GOALS
"""Scoreline cells per fixture: ``121``."""

_PROBABILITY_TOLERANCE: Final = 1e-9
"""How far a probability row may sit from a total of one."""

_QUANTILES: Final = (0.05, 0.25, 0.5, 0.75, 0.95)
"""Reported point quantiles, in ascending order."""


@dataclass(frozen=True, slots=True)
class Qualification:
    """Where the continental places and the relegation places start in a table.

    ``ucl_positions`` and ``europe_positions`` count places from the top, so a
    team qualifies when its final position is at most that number;
    ``relegation_positions`` counts places from the bottom.
    """

    ucl_positions: int = 4
    europe_positions: int = 6
    relegation_positions: int = 3


@dataclass(frozen=True, slots=True)
class SimFixture:
    """One match the simulation plays, with the date it is played on."""

    match_id: int
    home_team_id: int
    away_team_id: int
    sim_date: datetime.date
    """The virtual date the fixture is played on; it dates the simulated result."""


@dataclass(frozen=True, slots=True)
class TeamSimulation:
    """What the runs say about one team's final points and final position.

    ``points_pmf[i]`` is the probability of the final total ``points_min + i``, so
    the array covers every total the team reached and no gap. ``position_probs[i]``
    is the probability of finishing at position ``i + 1``, one entry per team of
    the league.
    """

    team_id: int
    mean_points: float
    points_min: int
    points_p05: int
    points_p25: int
    points_p50: int
    points_p75: int
    points_p95: int
    points_pmf: NDArray[np.float64]
    mean_position: float
    position_probs: NDArray[np.float64]
    p_title: float
    """Probability of finishing first."""
    p_ucl: float
    """Probability of a place at or above ``ucl_positions``."""
    p_europe: float
    """Probability of a place at or above ``europe_positions``."""
    p_relegation: float
    """Probability of a place among the ``relegation_positions`` bottom places."""


@dataclass(frozen=True, slots=True)
class SeasonSimulation:
    """One simulation result: the projected final table, in table order."""

    n_runs: int
    seed: int
    n_fixtures: int
    teams: tuple[TeamSimulation, ...]
    """One entry per team, projected table order first."""


def simulate_season(
    teams: Mapping[int, str],
    fixed: Sequence[MatchResult],
    fixtures: Sequence[SimFixture],
    score_probs: NDArray[np.float64],
    *,
    n_runs: int,
    seed: int,
    qualification: Qualification,
) -> SeasonSimulation:
    """Simulate ``fixtures`` ``n_runs`` times and report each team's totals.

    ``teams`` maps every team id of the league to its name and breaks ties that
    points and goals leave level. ``fixed`` holds results already played: they
    count in every run and are never sampled. ``fixtures`` holds the matches
    still to play, and ``score_probs[i]`` is the scoreline distribution of
    ``fixtures[i]``, so the two arguments must be built together.

    Both input orderings are normalized before anything is sampled: fixtures are
    played in ``(sim_date, match_id)`` order with their probability rows permuted
    to match, and the fixed results are sorted once. Every run then ranks
    ``fixed`` plus its own sampled results with
    :func:`lmi_core.standings.compute_standings` and contributes one point total
    and one position per team.

    The result is the projected final table: teams ordered by
    ``mean_position`` ascending, then ``mean_points`` descending, then team name,
    then team id.

    Raises :class:`ValueError` when ``n_runs`` is below one, ``score_probs`` does
    not have shape ``(len(fixtures), 11, 11)``, a probability row is negative or
    does not sum to one within ``1e-9``, ``teams`` is empty, the qualification
    thresholds do not fit the league, or a fixture names a team missing from
    ``teams``.
    """
    if n_runs < 1:
        raise ValueError(f"n_runs must be at least 1, got {n_runs}")
    if not teams:
        raise ValueError("teams must not be empty")
    _validate_qualification(qualification, len(teams))
    ordered, permutation = _ordered_fixtures(fixtures)
    probs = _validated_probs(score_probs, len(ordered))
    _validate_fixture_teams(ordered, teams)

    probs = probs[permutation]
    fixed_results = sorted(fixed, key=_fixed_order_key)
    home_goals, away_goals = _sample(probs, n_runs=n_runs, seed=seed)

    team_ids = tuple(teams)
    column_of = {team_id: column for column, team_id in enumerate(team_ids)}
    n_fixtures = len(ordered)
    points = np.zeros((n_runs, len(team_ids)), dtype=np.int64)
    places = np.zeros((n_runs, len(team_ids)), dtype=np.int64)
    for run in range(n_runs):
        played = [
            *fixed_results,
            *_run_results(ordered, home_goals[run], away_goals[run]),
        ]
        for row in compute_standings(teams, played):
            column = column_of[row.team_id]
            points[run, column] = row.points
            places[run, column] = row.position

    simulations = tuple(
        _team_simulation(
            team_id,
            points=points[:, column],
            places=places[:, column],
            n_runs=n_runs,
            n_teams=len(team_ids),
            qualification=qualification,
        )
        for column, team_id in enumerate(team_ids)
    )
    projected = tuple(sorted(simulations, key=lambda sim: _projected_order_key(sim, teams)))
    return SeasonSimulation(n_runs=n_runs, seed=seed, n_fixtures=n_fixtures, teams=projected)


def _ordered_fixtures(
    fixtures: Sequence[SimFixture],
) -> tuple[list[SimFixture], NDArray[np.intp]]:
    """Return the fixtures in ``(sim_date, match_id)`` order with that permutation.

    The permutation indexes the caller's ``score_probs`` rows, so applying it to
    both keeps every probability row attached to the fixture it describes.
    """
    order = sorted(range(len(fixtures)), key=lambda index: _fixture_order_key(fixtures[index]))
    permutation = np.array(order, dtype=np.intp)
    return [fixtures[index] for index in order], permutation


def _fixture_order_key(fixture: SimFixture) -> tuple[datetime.date, int]:
    """Return the key that fixes when a fixture is played."""
    return (fixture.sim_date, fixture.match_id)


def _fixed_order_key(result: MatchResult) -> tuple[datetime.date, int, int, int, int]:
    """Return a total key over a played result, so the fixed list has one order."""
    return (
        result.kickoff_date,
        result.home_team_id,
        result.away_team_id,
        result.home_goals,
        result.away_goals,
    )


def _validated_probs(score_probs: NDArray[np.float64], n_fixtures: int) -> NDArray[np.float64]:
    """Return ``score_probs`` as a float64 matrix after checking its shape and rows."""
    probs = np.asarray(score_probs, dtype=np.float64)
    expected_shape = (n_fixtures, _SCORELINE_GOALS, _SCORELINE_GOALS)
    if probs.shape != expected_shape:
        raise ValueError(f"score_probs must have shape {expected_shape}, got {probs.shape}")
    rows = probs.reshape(n_fixtures, _SCORELINE_CELLS)
    negative = np.argwhere(rows < 0)
    if negative.size:
        index = int(negative[0][0])
        cell = int(negative[0][1])
        raise ValueError(
            f"score_probs row {index} has a negative probability {float(rows[index, cell])} "
            f"at scoreline {cell // _SCORELINE_GOALS}-{cell % _SCORELINE_GOALS}"
        )
    sums = rows.sum(axis=1)
    off_total = np.flatnonzero(np.abs(sums - 1.0) > _PROBABILITY_TOLERANCE)
    if off_total.size:
        index = int(off_total[0])
        raise ValueError(
            f"score_probs row {index} sums to {float(sums[index])}, "
            f"not 1 within {_PROBABILITY_TOLERANCE}"
        )
    return probs


def _validate_qualification(qualification: Qualification, n_teams: int) -> None:
    """Check that the thresholds carve the league into non-empty regions."""
    ucl_positions = qualification.ucl_positions
    europe_positions = qualification.europe_positions
    relegation_positions = qualification.relegation_positions
    if not 1 <= ucl_positions <= europe_positions < n_teams:
        raise ValueError(
            "qualification needs 1 <= ucl_positions <= europe_positions < n_teams, "
            f"got ucl_positions={ucl_positions} and europe_positions={europe_positions} "
            f"for {n_teams} teams"
        )
    if not 1 <= relegation_positions < n_teams:
        raise ValueError(
            "qualification needs 1 <= relegation_positions < n_teams, "
            f"got relegation_positions={relegation_positions} for {n_teams} teams"
        )


def _validate_fixture_teams(fixtures: Sequence[SimFixture], teams: Mapping[int, str]) -> None:
    """Check that every simulated fixture names known teams.

    A fixture that pairs a team with itself is not caught here: it reaches
    ``compute_standings``, which rejects it.
    """
    for fixture in fixtures:
        for team_id in (fixture.home_team_id, fixture.away_team_id):
            if team_id not in teams:
                raise ValueError(
                    f"fixture {fixture.match_id} names team {team_id}, which is not in teams"
                )


def _sample(
    probs: NDArray[np.float64], *, n_runs: int, seed: int
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Draw one scoreline per fixture and run, from a single seeded stream.

    ``u`` is uniform over ``(0, 1)``; the inverse of the cumulative distribution
    of each fixture row turns it into a scoreline. The index is clamped because a
    ``u`` just below one can land above the last cumulative value after rounding,
    and ``121`` would be out of the grid.
    """
    rng = np.random.Generator(np.random.PCG64(seed))
    n_fixtures = probs.shape[0]
    uniforms = rng.random((n_runs, n_fixtures))
    cumulative = np.cumsum(probs.reshape(n_fixtures, _SCORELINE_CELLS), axis=1)
    home_goals = np.empty((n_runs, n_fixtures), dtype=np.int64)
    away_goals = np.empty((n_runs, n_fixtures), dtype=np.int64)
    for index in range(n_fixtures):
        cell = np.searchsorted(cumulative[index], uniforms[:, index], side="right")
        cell = np.clip(cell, 0, _SCORELINE_CELLS - 1)
        home_goals[:, index] = cell // _SCORELINE_GOALS
        away_goals[:, index] = cell % _SCORELINE_GOALS
    return home_goals, away_goals


def _run_results(
    fixtures: Sequence[SimFixture],
    home_goals: NDArray[np.int64],
    away_goals: NDArray[np.int64],
) -> list[MatchResult]:
    """Turn one run's sampled scorelines into results dated as their fixture."""
    results: list[MatchResult] = []
    for fixture, home, away in zip(fixtures, home_goals, away_goals, strict=True):
        results.append(
            MatchResult(
                home_team_id=fixture.home_team_id,
                away_team_id=fixture.away_team_id,
                home_goals=int(home),
                away_goals=int(away),
                kickoff_date=fixture.sim_date,
            )
        )
    return results


def _team_simulation(
    team_id: int,
    *,
    points: NDArray[np.int64],
    places: NDArray[np.int64],
    n_runs: int,
    n_teams: int,
    qualification: Qualification,
) -> TeamSimulation:
    """Summarize one team's point totals and finishing positions over the runs."""
    points_min = int(points.min())
    points_max = int(points.max())
    counts = np.bincount(points - points_min, minlength=points_max - points_min + 1)
    position_counts = np.bincount(places, minlength=n_teams + 1)[1:]
    position_probs = position_counts / n_runs
    quantiles = np.asarray(np.quantile(points, _QUANTILES, method="inverted_cdf"), dtype=np.float64)
    return TeamSimulation(
        team_id=team_id,
        mean_points=float(points.mean()),
        points_min=points_min,
        points_p05=int(quantiles[0]),
        points_p25=int(quantiles[1]),
        points_p50=int(quantiles[2]),
        points_p75=int(quantiles[3]),
        points_p95=int(quantiles[4]),
        points_pmf=counts / n_runs,
        mean_position=float(places.mean()),
        position_probs=position_probs,
        p_title=float(position_probs[0]),
        p_ucl=float(position_probs[: qualification.ucl_positions].sum()),
        p_europe=float(position_probs[: qualification.europe_positions].sum()),
        p_relegation=float(position_probs[n_teams - qualification.relegation_positions :].sum()),
    )


def _projected_order_key(
    simulation: TeamSimulation, teams: Mapping[int, str]
) -> tuple[float, float, str, int]:
    """Return the sort key of the projected table: mean position, then mean points, name, id."""
    return (
        simulation.mean_position,
        -simulation.mean_points,
        teams[simulation.team_id],
        simulation.team_id,
    )
