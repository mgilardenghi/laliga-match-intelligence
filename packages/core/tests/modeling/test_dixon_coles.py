"""Tests for the Dixon-Coles goal model.

The formula checks use an independent reference: ``scipy.stats.poisson.pmf`` for
the Poisson part, and the four ``tau`` cells written out by hand. The gradient is
checked against finite differences of the module's own objective, and the
recovery test generates matches from known parameters with its own sampling code,
so a shared bug cannot make both sides agree.
"""

import datetime
import time

import numpy as np
import pytest
from lmi_core.evaluation.walkforward import weekly_cutoffs
from lmi_core.modeling.dixon_coles import (
    DixonColesModel,
    negative_log_likelihood,
    score_matrix,
    tau_correction,
    time_weight,
)
from lmi_core.modeling.types import Fixtures, History, LeakageError
from numpy.typing import NDArray
from scipy.optimize import check_grad
from scipy.stats import poisson

from modeling.leakage_harness import (
    assert_fit_predict_invariant,
    assert_walk_forward_invariant,
    history_before,
    make_fixtures,
    make_history,
    make_synthetic_league,
    to_date,
)

SEED = 5
CUTOFF = datetime.date(2021, 1, 9)
_GOALS_GRID = 11
_SAMPLING_GRID = 16
"""Grid the recovery test samples its scorelines from; wider than the model's."""


def _history_from_columns(
    kickoff_date: NDArray[np.datetime64],
    home_team_id: NDArray[np.int64],
    away_team_id: NDArray[np.int64],
    home_goals: NDArray[np.int64],
    away_goals: NDArray[np.int64],
    *,
    season_id: NDArray[np.int64] | None = None,
) -> History:
    """Return a sorted history with fresh match ids and the result derived from goals."""
    order = np.lexsort((np.arange(1, home_goals.size + 1), kickoff_date))
    scored = home_goals[order]
    conceded = away_goals[order]
    result = np.where(scored > conceded, 0, np.where(scored == conceded, 1, 2)).astype(np.int8)
    n = int(scored.size)
    seasons = np.ones(n, dtype=np.int64) if season_id is None else season_id[order]
    return History(
        match_id=np.arange(1, n + 1, dtype=np.int64),
        kickoff_date=kickoff_date[order],
        season_id=seasons,
        home_team_id=home_team_id[order],
        away_team_id=away_team_id[order],
        odds=np.full((n, 3), np.nan, dtype=np.float64),
        odds_source=tuple(None for _ in range(n)),
        home_goals=scored,
        away_goals=conceded,
        result=result,
    )


def _weekly_schedule(
    teams: list[int],
    *,
    start: datetime.date,
    season_id: int,
    repetitions: int,
) -> tuple[list[int], list[int], list[int], list[datetime.date]]:
    """Return a repeated double round robin on weekly matchdays."""
    home_ids: list[int] = []
    away_ids: list[int] = []
    season_ids: list[int] = []
    dates: list[datetime.date] = []
    week = 0
    for _ in range(repetitions):
        for home in teams:
            for away in teams:
                if home == away:
                    continue
                home_ids.append(home)
                away_ids.append(away)
                season_ids.append(season_id)
                dates.append(start + datetime.timedelta(days=7 * week))
                week += 1
    return home_ids, away_ids, season_ids, dates


def _strength_history(
    *,
    season_id: list[int],
    home_team_id: list[int],
    away_team_id: list[int],
    kickoff_date: list[datetime.date],
    alpha: dict[int, float],
    beta: dict[int, float],
    mu: float,
    gamma: float,
    seed: int,
) -> History:
    """Return a history whose goals are Poisson draws from known team strengths."""
    generator = np.random.default_rng(seed)
    n = len(home_team_id)
    home_goals = np.empty(n, dtype=np.int64)
    away_goals = np.empty(n, dtype=np.int64)
    for index, (home, away) in enumerate(zip(home_team_id, away_team_id, strict=True)):
        rate_home = float(np.exp(mu + gamma + alpha[home] - beta[away]))
        rate_away = float(np.exp(mu + alpha[away] - beta[home]))
        home_goals[index] = generator.poisson(rate_home)
        away_goals[index] = generator.poisson(rate_away)
    return _history_from_columns(
        np.array([np.datetime64(day, "D") for day in kickoff_date], dtype="datetime64[D]"),
        np.array(home_team_id, dtype=np.int64),
        np.array(away_team_id, dtype=np.int64),
        home_goals,
        away_goals,
        season_id=np.array(season_id, dtype=np.int64),
    )


def _history_with_dates(
    *,
    seed: int,
    n_matches: int,
    n_teams: int,
    n_dates: int,
    last_day: datetime.date,
) -> History:
    """Return a history spread over ``n_dates`` weekly matchdays before ``last_day``."""
    generator = np.random.default_rng(seed)
    team_ids = np.arange(1, n_teams + 1, dtype=np.int64)
    home = generator.choice(team_ids, size=n_matches)
    away = generator.choice(team_ids, size=n_matches)
    same = home == away
    away[same] = home[same] % n_teams + 1
    home_goals = generator.poisson(1.5, size=n_matches).astype(np.int64)
    away_goals = generator.poisson(1.1, size=n_matches).astype(np.int64)
    day_index = np.arange(n_matches) % n_dates
    kickoff = np.array(
        [
            np.datetime64(last_day - datetime.timedelta(days=7 * (n_dates - 1 - int(i))), "D")
            for i in day_index
        ],
        dtype="datetime64[D]",
    )
    return _history_from_columns(kickoff, home, away, home_goals, away_goals)


def _fixtures_for_teams(
    home_team_id: NDArray[np.int64],
    away_team_id: NDArray[np.int64],
    kickoff_date: datetime.date,
) -> Fixtures:
    """Return fixtures with the given teams, all on one date after the cutoff."""
    n = int(home_team_id.size)
    return Fixtures(
        match_id=np.arange(1, n + 1, dtype=np.int64),
        kickoff_date=np.full(n, np.datetime64(kickoff_date, "D"), dtype="datetime64[D]"),
        season_id=np.ones(n, dtype=np.int64),
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        odds=np.full((n, 3), np.nan, dtype=np.float64),
        odds_source=tuple(None for _ in range(n)),
    )


def _sampling_matrix(rate_home: float, rate_away: float, rho: float) -> NDArray[np.float64]:
    """Return the tau-corrected score distribution the recovery test samples from."""
    goals = np.arange(_SAMPLING_GRID, dtype=np.float64)
    matrix = np.outer(poisson.pmf(goals, rate_home), poisson.pmf(goals, rate_away))
    matrix[0, 0] *= 1.0 - rate_home * rate_away * rho
    matrix[0, 1] *= 1.0 + rate_home * rho
    matrix[1, 0] *= 1.0 + rate_away * rho
    matrix[1, 1] *= 1.0 - rho
    floored = np.clip(matrix, 1e-10, None)
    return floored / floored.sum()


def test_tau_correction_matches_the_low_score_formula() -> None:
    rate_home, rate_away, rho = 1.7, 0.9, -0.13
    scores = [(0, 0), (1, 0), (0, 1), (1, 1), (2, 0), (0, 2), (2, 2), (5, 3)]
    home = np.array([score[0] for score in scores], dtype=np.int64)
    away = np.array([score[1] for score in scores], dtype=np.int64)

    tau = tau_correction(home, away, rate_home, rate_away, rho)

    expected = np.array(
        [
            1.0 - rate_home * rate_away * rho,
            1.0 + rate_away * rho,
            1.0 + rate_home * rho,
            1.0 - rho,
            1.0,
            1.0,
            1.0,
            1.0,
        ]
    )
    np.testing.assert_allclose(tau, expected, rtol=0.0, atol=1e-12)


def test_score_matrix_without_correction_is_the_independent_poisson_product() -> None:
    rate_home, rate_away = 2.0, 2.5
    goals = np.arange(_GOALS_GRID, dtype=np.float64)
    reference = np.outer(poisson.pmf(goals, rate_home), poisson.pmf(goals, rate_away))
    reference = reference / reference.sum()

    matrix = score_matrix(np.array([rate_home]), np.array([rate_away]), 0.0)

    assert matrix.shape == (1, _GOALS_GRID, _GOALS_GRID)
    np.testing.assert_allclose(matrix[0], reference, rtol=0.0, atol=1e-12)


@pytest.mark.parametrize(
    ("rate_home", "rate_away"),
    [(20.0, 20.0), (1.35, 1.35), (0.4, 3.0)],
)
def test_score_matrix_without_correction_keeps_the_poisson_masses(
    rate_home: float, rate_away: float
) -> None:
    """A cell floor would lift the tiny high-score masses; with rho = 0 none is moved."""
    goals = np.arange(_GOALS_GRID, dtype=np.float64)
    reference = np.outer(poisson.pmf(goals, rate_home), poisson.pmf(goals, rate_away))
    reference = reference / reference.sum()

    matrix = score_matrix(np.array([rate_home]), np.array([rate_away]), 0.0)

    np.testing.assert_allclose(matrix[0], reference, rtol=0.0, atol=1e-12)


def test_score_matrix_raises_when_the_grid_has_no_positive_mass() -> None:
    with pytest.raises(ValueError, match="no positive mass"):
        score_matrix(np.array([1000.0]), np.array([1000.0]), 0.0)


@pytest.mark.parametrize("rho", [-0.25, 0.0, 0.25])
def test_score_matrix_rows_are_distributions(rho: float) -> None:
    rate_home = np.array([0.2, 1.4, 3.0, 9.0])
    rate_away = np.array([0.1, 2.2, 0.7, 8.0])

    matrix = score_matrix(rate_home, rate_away, rho)

    assert matrix.shape == (4, _GOALS_GRID, _GOALS_GRID)
    assert bool((matrix >= 0.0).all())
    np.testing.assert_allclose(matrix.sum(axis=(1, 2)), np.ones(4), rtol=0.0, atol=1e-12)


def test_time_weight_decays_exponentially() -> None:
    weights = time_weight(np.array([0, 365], dtype=np.int64))

    assert weights[0] == 1.0
    np.testing.assert_allclose(weights[1], np.exp(-0.0019 * 365), rtol=0.0, atol=1e-15)


def test_analytic_gradient_matches_finite_differences() -> None:
    team_count = 6
    history = _history_with_dates(
        seed=3, n_matches=60, n_teams=team_count, n_dates=6, last_day=CUTOFF
    )
    generator = np.random.default_rng(19)
    theta = np.zeros(3 + 2 * team_count, dtype=np.float64)
    theta[0] = 0.2
    theta[1] = 0.25
    theta[2 : 2 + team_count] = generator.uniform(-0.4, 0.4, team_count)
    theta[2 + team_count : 2 + 2 * team_count] = generator.uniform(-0.4, 0.4, team_count)
    theta[-1] = -0.1

    def objective(parameters: NDArray[np.float64]) -> float:
        return negative_log_likelihood(parameters, history, CUTOFF)[0]

    def gradient(parameters: NDArray[np.float64]) -> NDArray[np.float64]:
        return negative_log_likelihood(parameters, history, CUTOFF)[1]

    error = check_grad(objective, gradient, theta)

    print(f"check_grad error: {error:.3e}")
    assert error < 1e-4


def test_parameters_are_recovered_from_synthetic_matches() -> None:
    n_teams = 16
    n_matches = 4000
    gamma_true = 0.25
    rho_true = -0.13
    mu_true = float(np.log(1.35))
    generator = np.random.default_rng(2024)
    alpha_true = generator.normal(0.0, 0.2, n_teams)
    beta_true = generator.normal(0.0, 0.2, n_teams)
    alpha_true -= alpha_true.mean()
    beta_true -= beta_true.mean()

    home = generator.integers(0, n_teams, n_matches)
    away = generator.integers(0, n_teams, n_matches)
    same = home == away
    away[same] = (home[same] + 1) % n_teams
    rate_home = np.exp(mu_true + gamma_true + alpha_true[home] - beta_true[away])
    rate_away = np.exp(mu_true + alpha_true[away] - beta_true[home])

    home_goals = np.empty(n_matches, dtype=np.int64)
    away_goals = np.empty(n_matches, dtype=np.int64)
    for index in range(n_matches):
        matrix = _sampling_matrix(float(rate_home[index]), float(rate_away[index]), rho_true)
        flat = matrix.ravel()
        cumulative = np.cumsum(flat)
        drawn = int(np.searchsorted(cumulative, float(generator.random()) * flat.sum()))
        cell = min(drawn, flat.size - 1)
        home_goals[index], away_goals[index] = divmod(cell, _SAMPLING_GRID)

    days_before = generator.integers(1, 366, n_matches)
    kickoff = np.array(
        [np.datetime64(CUTOFF - datetime.timedelta(days=int(days)), "D") for days in days_before],
        dtype="datetime64[D]",
    )
    history = _history_from_columns(
        kickoff,
        (home + 1).astype(np.int64),
        (away + 1).astype(np.int64),
        home_goals,
        away_goals,
    )

    model = DixonColesModel(seed=SEED)
    model.fit(history, CUTOFF)

    parameters = model.parameters
    assert parameters is not None
    gamma_error = abs(parameters.gamma - gamma_true)
    rho_error = abs(parameters.rho - rho_true)
    print(f"gamma error: {gamma_error:.4f}")
    print(f"rho error: {rho_error:.4f}")
    assert gamma_error <= 0.05
    assert rho_error <= 0.05


def _assert_newcomer_row_matches_rule(
    model: DixonColesModel,
    recent_team_ids: NDArray[np.int64],
    *,
    excluded_team_ids: NDArray[np.int64],
) -> None:
    """Assert the newcomer row is the one the documented rule implies.

    The rule is applied here from the fitted parameters: pick the three teams
    with the lowest ``alpha - beta`` among the teams that played in the most
    recent season (all of them when fewer than three exist) and average their
    ``alpha`` and ``beta``. The caller built the history, so it knows which teams
    belong to that season. The expected row comes from the module's own
    :func:`score_matrix`, which is not the rule under test.
    """
    parameters = model.parameters
    assert parameters is not None
    net = parameters.alpha - parameters.beta
    candidates = np.flatnonzero(np.isin(parameters.team_ids, recent_team_ids))
    order = candidates[np.argsort(net[candidates], kind="stable")]
    chosen = order[: min(3, order.size)]
    expected_alpha = float(np.mean(parameters.alpha[chosen]))
    expected_beta = float(np.mean(parameters.beta[chosen]))
    assert np.hypot(expected_alpha, expected_beta) > 0.1
    assert abs(expected_alpha - expected_beta) > 0.1, (
        "the inherited strengths must differ from the zero fallback by a margin "
        "that makes the row comparison meaningful"
    )
    for team_id in excluded_team_ids:
        index = int(np.searchsorted(parameters.team_ids, int(team_id)))
        assert parameters.team_ids[index] == team_id
        assert float(net[index]) < float(net[candidates].min()), (
            "an older-season team must be weaker than every recent team, or dropping "
            "the season filter would leave the inherited strengths unchanged"
        )

    fixtures = _fixtures_for_teams(
        home_team_id=np.array([901], dtype=np.int64),
        away_team_id=np.array([902], dtype=np.int64),
        kickoff_date=CUTOFF + datetime.timedelta(days=1),
    )
    observed = model.predict(fixtures)[0]

    rate_home = float(np.exp(parameters.mu + parameters.gamma + expected_alpha - expected_beta))
    rate_away = float(np.exp(parameters.mu + expected_alpha - expected_beta))
    matrix = score_matrix(np.array([rate_home]), np.array([rate_away]), parameters.rho)[0]
    expected = np.array(
        [
            np.tril(matrix, -1).sum(),
            np.diagonal(matrix).sum(),
            np.triu(matrix, 1).sum(),
        ]
    )

    assert np.isfinite(observed).all()
    np.testing.assert_allclose(observed.sum(), 1.0, rtol=0.0, atol=1e-12)
    np.testing.assert_allclose(observed, expected, rtol=0.0, atol=1e-12)


def test_newcomer_inherits_the_weakest_teams_of_the_recent_season() -> None:
    alpha = {1: 0.6, 2: 0.3, 3: 0.0, 4: -0.3, 5: -0.6, 6: -1.5}
    beta = {1: -0.4, 2: -0.2, 3: 0.0, 4: 0.2, 5: 0.4, 6: 1.5}
    mu, gamma = 0.1, 0.2
    old_home, old_away, old_seasons, old_dates = _weekly_schedule(
        [1, 2, 3, 4, 5, 6],
        start=CUTOFF - datetime.timedelta(days=1095),
        season_id=1,
        repetitions=2,
    )
    recent_home, recent_away, recent_seasons, recent_dates = _weekly_schedule(
        [1, 2, 3, 4, 5], start=CUTOFF - datetime.timedelta(days=600), season_id=2, repetitions=3
    )
    history = _strength_history(
        season_id=old_seasons + recent_seasons,
        home_team_id=old_home + recent_home,
        away_team_id=old_away + recent_away,
        kickoff_date=old_dates + recent_dates,
        alpha=alpha,
        beta=beta,
        mu=mu,
        gamma=gamma,
        seed=SEED,
    )

    model = DixonColesModel(seed=SEED)
    model.fit(history, CUTOFF)

    _assert_newcomer_row_matches_rule(
        model,
        np.array([1, 2, 3, 4, 5], dtype=np.int64),
        excluded_team_ids=np.array([6], dtype=np.int64),
    )


def test_newcomer_inherits_both_recent_teams_when_fewer_than_three() -> None:
    alpha = {8: 0.8, 9: -0.4, 10: -2.0}
    beta = {8: -0.5, 9: 0.3, 10: 1.5}
    mu, gamma = 0.1, 0.2
    old_home, old_away, old_seasons, old_dates = _weekly_schedule(
        [8, 9, 10], start=CUTOFF - datetime.timedelta(days=1000), season_id=1, repetitions=6
    )
    recent_home, recent_away, recent_seasons, recent_dates = _weekly_schedule(
        [8, 9], start=CUTOFF - datetime.timedelta(days=600), season_id=2, repetitions=20
    )
    history = _strength_history(
        season_id=old_seasons + recent_seasons,
        home_team_id=old_home + recent_home,
        away_team_id=old_away + recent_away,
        kickoff_date=old_dates + recent_dates,
        alpha=alpha,
        beta=beta,
        mu=mu,
        gamma=gamma,
        seed=SEED,
    )

    model = DixonColesModel(seed=SEED)
    model.fit(history, CUTOFF)

    _assert_newcomer_row_matches_rule(
        model,
        np.array([8, 9], dtype=np.int64),
        excluded_team_ids=np.array([10], dtype=np.int64),
    )


@pytest.mark.parametrize("match_count", [0, 1, 2])
def test_empty_and_tiny_windows_still_predict(match_count: int) -> None:
    model = DixonColesModel(seed=SEED)
    model.fit(make_history([0] * match_count), CUTOFF)

    probabilities = model.predict(make_fixtures(3))

    assert probabilities.shape == (3, 3)
    assert np.isfinite(probabilities).all()
    assert bool((probabilities >= 0.0).all())
    np.testing.assert_allclose(probabilities.sum(axis=1), np.ones(3), rtol=0.0, atol=1e-12)
    parameters = model.parameters
    assert parameters is not None
    if match_count == 0:
        assert (parameters.mu, parameters.gamma, parameters.rho) == (0.0, 0.0, 0.0)
        assert parameters.team_ids.size == 0


def test_fit_window_lower_bound_is_inclusive() -> None:
    inside = CUTOFF - datetime.timedelta(days=200)
    kickoff = np.array(
        [
            np.datetime64(CUTOFF - datetime.timedelta(days=1096), "D"),
            np.datetime64(CUTOFF - datetime.timedelta(days=1095), "D"),
            np.datetime64(inside, "D"),
            np.datetime64(inside + datetime.timedelta(days=1), "D"),
        ],
        dtype="datetime64[D]",
    )
    history = _history_from_columns(
        kickoff,
        np.array([11, 13, 1, 2], dtype=np.int64),
        np.array([12, 14, 2, 1], dtype=np.int64),
        np.array([1, 2, 1, 0], dtype=np.int64),
        np.array([1, 0, 0, 2], dtype=np.int64),
    )

    model = DixonColesModel(seed=SEED)
    model.fit(history, CUTOFF)

    parameters = model.parameters
    assert parameters is not None
    fitted = {int(team_id) for team_id in parameters.team_ids}
    assert fitted == {1, 2, 13, 14}


def test_fit_rejects_history_on_the_cutoff_day() -> None:
    history = _history_from_columns(
        np.array([np.datetime64(CUTOFF, "D")], dtype="datetime64[D]"),
        np.array([1], dtype=np.int64),
        np.array([2], dtype=np.int64),
        np.array([1], dtype=np.int64),
        np.array([0], dtype=np.int64),
    )

    with pytest.raises(LeakageError):
        DixonColesModel(seed=SEED).fit(history, CUTOFF)


def test_describe_reports_the_identity_and_the_fixed_values() -> None:
    model = DixonColesModel(seed=13)

    assert model.parameters is None
    assert model.describe() == {
        "name": "dixon_coles",
        "version": "1",
        "hyperparameters": {
            "seed": 13,
            "time_decay_xi": 0.0019,
            "window_days": 1095,
            "l2_penalty": 0.01,
            "rho_bound": 0.25,
            "max_goals": 10,
        },
    }


def test_fit_predict_invariance() -> None:
    assert_fit_predict_invariant("dixon_coles")


def test_walk_forward_invariance() -> None:
    assert_walk_forward_invariant("dixon_coles")


def test_fifty_weekly_fits_finish_quickly() -> None:
    data = make_synthetic_league(n_teams=20, n_seasons=3)
    first = to_date(data.kickoff_date[0])
    last = to_date(data.kickoff_date[-1])
    cutoffs = weekly_cutoffs(first, last)[-50:]
    assert len(cutoffs) == 50

    started = time.perf_counter()
    for cutoff in cutoffs:
        model = DixonColesModel(seed=SEED)
        model.fit(history_before(data, cutoff), cutoff)
    elapsed = time.perf_counter() - started

    print(f"50 weekly fits took {elapsed:.2f} s")
    assert elapsed < 60.0
