"""The Dixon-Coles goal model: a bivariate Poisson with a low-score correction.

Each team gets an attack strength ``alpha`` and a defence strength ``beta``, and
the goal rates of a match are

    lambda_home = exp(mu + gamma + alpha[home] - beta[away])
    lambda_away = exp(mu + alpha[away] - beta[home])

where ``mu`` is the league intercept and ``gamma`` the home advantage. Goals are
independent Poisson draws, corrected on the four low scores by ``tau``, after
Dixon and Coles (1997, JRSS C 46(2), 265-276):

    tau(0, 0) = 1 - lambda_home * lambda_away * rho
    tau(0, 1) = 1 + lambda_home * rho
    tau(1, 0) = 1 + lambda_away * rho
    tau(1, 1) = 1 - rho
    tau(h, a) = 1 otherwise

Convention: ``rho`` is negative when the low draws 0-0 and 1-1 are more frequent
than independent Poisson predicts, and 1-0 and 0-1 less frequent, which is the
sign football data reports. ``tau_correction`` returns the formula unclipped;
:func:`score_matrix` sets a negative raw cell to zero so that a large rate with a
positive ``rho`` cannot produce a negative probability, and keeps every positive
cell unchanged. The method :meth:`DixonColesModel.score_matrix` returns the same
matrix for the fixtures of a fitted model, so a caller that needs the matrix and
a caller that needs the H/D/A rows see one score distribution, not two.

Fit: the window is ``cutoff - 1095 days <= kickoff_date < cutoff``, so the fit
never sees a match dated on or after its cutoff and ignores matches older than
three years. Each match in the window carries the weight
``exp(-0.0019 * (cutoff - kickoff_date).days)``. The objective is the weighted
negative log-likelihood over the window plus ``0.01 * sum(alpha**2 + beta**2)``,
minimized with L-BFGS-B, an analytic gradient, ``rho`` bounded to
``[-0.25, 0.25]`` and nothing else bounded.

The fit is hard about its answer: a returned vector of the wrong length, or with
an entry that is not finite, raises
:class:`~lmi_core.modeling.elo.ModelFitError` instead of falling back to a
replacement answer. A ``success=False`` status with usable finite parameters is
only a warning, because L-BFGS-B reports it (``ABNORMAL_TERMINATION_IN_LNSRCH``)
with perfectly usable parameters and the measured count on real data is zero. The
linear predictors of an accepted fit are clipped before they are exponentiated
(:data:`_MAX_EXP_ARGUMENT`), so even an extreme vector gives a huge but finite
objective rather than ``inf`` and ``nan``; the clip never binds within the fitted
range.

Parameter vector layout: ``theta = [mu, gamma, alpha_0..alpha_{K-1},
beta_0..beta_{K-1}, rho]``, where ``K`` is the number of distinct team ids that
appear as home or away in the window and index ``k`` is the position of the team
id in the sorted unique id array (contiguous, ``np.searchsorted``). The initial
vector is fixed and never a warm start: zeros for ``alpha``, ``beta``, ``gamma``
and ``rho``, and ``mu = log(goals in the window / (2 * matches in the window))``.

Newcomers: a fixture whose team does not appear in the window uses the mean
``alpha`` and the mean ``beta`` of the three fitted teams with the lowest
``alpha - beta`` among the teams that played in the most recent season of the
window (fewer than three such teams uses all of them). With an empty window both
values are 0, so the fixture is predicted from ``mu = gamma = rho = 0``.

Determinism: the fit is a deterministic optimization and the seed is recorded
only; no global random state is read or written.
"""

import datetime
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.optimize import Bounds, minimize
from scipy.stats import poisson

from lmi_core.modeling.base import Model
from lmi_core.modeling.elo import ModelFitError
from lmi_core.modeling.types import Fixtures, History

logger = logging.getLogger(__name__)
"""Logs the one optimizer status that is a warning rather than a fit error."""

TIME_DECAY_XI: Final = 0.0019
"""Decay of the time weight per day of age: ``w = exp(-xi * days)``."""

WINDOW_DAYS: Final = 1095
"""Length of the fitting window in days (three years)."""

L2_PENALTY: Final = 0.01
"""Factor of the ridge penalty on ``alpha`` and ``beta``."""

RHO_BOUND: Final = 0.25
"""Half-width of the box constraint on ``rho``."""

MAX_GOALS: Final = 10
"""Highest goal count per side that the score matrix covers."""

_TAU_FLOOR: Final = 1e-10
"""Smallest ``tau`` that the log-likelihood uses, so the log stays finite."""

_MAX_EXP_ARGUMENT: Final = 300.0
"""Clip of a linear predictor before it is exponentiated.

``exp(709)`` is the last finite ``float64``, but a clip there still lets
``rate_home * rate_away`` (about ``1e304 * 1e304``) overflow, and that product
appears in ``tau`` and in ``d_log_rho``. ``exp(300)`` is about ``1.9e130``, which
keeps the product and its ratio to :data:`_TAU_FLOOR` finite. Fitted predictors
stay far below the clip: the largest ``|eta|`` measured on the walk-forward
backtest is about 4.4.
"""

_MIN_INITIAL_RATE: Final = 1e-3
"""Floor on the goals-per-team-per-match rate the initial ``mu`` is built from."""

_NEWCOMER_TEAMS: Final = 3
"""Number of teams whose average strength a newcomer inherits."""

_DRAW = 1
_HOME = 0
_AWAY = 2


@dataclass(frozen=True)
class DixonColesParameters:
    """The fitted parameters, with ``team_ids`` sorted so ``alpha``/``beta`` align.

    ``mu``, ``gamma`` and ``rho`` are scalars; ``alpha`` and ``beta`` have one
    entry per team, and index ``k`` belongs to ``team_ids[k]``.
    """

    mu: float
    gamma: float
    rho: float
    team_ids: NDArray[np.int64]
    alpha: NDArray[np.float64]
    beta: NDArray[np.float64]


def time_weight(days: NDArray[np.int64]) -> NDArray[np.float64]:
    """Return ``exp(-0.0019 * days)`` for each age in ``days``.

    Args:
        days: Non-negative age of each match in days, ``cutoff - kickoff_date``.

    Returns:
        One weight per age, 1 for a match on the cutoff day.
    """
    return np.exp(-TIME_DECAY_XI * np.asarray(days, dtype=np.float64))


def tau_correction(
    home_goals: ArrayLike,
    away_goals: ArrayLike,
    lambda_home: ArrayLike,
    lambda_away: ArrayLike,
    rho: float | ArrayLike,
) -> NDArray[np.float64]:
    """Return the Dixon-Coles ``tau`` of each score, unclipped.

    Args:
        home_goals: Home goal count, broadcastable against the other arguments.
        away_goals: Away goal count, broadcastable against the other arguments.
        lambda_home: Home goal rate, broadcastable against the other arguments.
        lambda_away: Away goal rate, broadcastable against the other arguments.
        rho: Low-score correlation; negative when low draws are over-represented.

    Returns:
        ``tau`` for each broadcast element: the four cells of the module
        docstring and ``1`` elsewhere. The value can be negative or zero; the
        caller decides how to floor it.
    """
    home, away, rate_home, rate_away, rho_value = np.broadcast_arrays(
        np.asarray(home_goals),
        np.asarray(away_goals),
        np.asarray(lambda_home, dtype=np.float64),
        np.asarray(lambda_away, dtype=np.float64),
        np.asarray(rho, dtype=np.float64),
    )
    tau = np.ones(home.shape, dtype=np.float64)
    tau = np.where((home == 0) & (away == 0), 1.0 - rate_home * rate_away * rho_value, tau)
    tau = np.where((home == 0) & (away == 1), 1.0 + rate_home * rho_value, tau)
    tau = np.where((home == 1) & (away == 0), 1.0 + rate_away * rho_value, tau)
    tau = np.where((home == 1) & (away == 1), 1.0 - rho_value, tau)
    return tau


def score_matrix(lambda_home: ArrayLike, lambda_away: ArrayLike, rho: float) -> NDArray[np.float64]:
    """Return one renormalized score matrix per fixture.

    Each cell is ``poisson_pmf(h; lambda_home[i]) * poisson_pmf(a; lambda_away[i])
    * tau(h, a)`` for ``h, a`` in ``0..10``. A negative raw cell (a large rate with
    a positive ``rho``) is set to zero before the matrix is renormalized, because a
    negative value is not a probability; every positive cell is kept unchanged, so
    the Poisson masses are not distorted. A matrix is renormalized by its own
    total, so every matrix sums to 1.

    Args:
        lambda_home: Home goal rate per fixture, or one scalar for one fixture.
        lambda_away: Away goal rate per fixture, in the same order.
        rho: Low-score correlation of the fit.

    Returns:
        An array of shape ``(n, 11, 11)``; entry ``[i, h, a]`` is the probability
        of the score ``h``-``a`` in fixture ``i``, and each matrix sums to 1.

    Raises:
        ValueError: the two rate arrays do not have the same length, or a fixture
            has no positive mass on the ``0..10`` grid because both Poisson pmfs
            underflow to zero at a rate far outside any fitted range.
    """
    rate_home = _as_rates(lambda_home)
    rate_away = _as_rates(lambda_away)
    if rate_home.shape != rate_away.shape:
        raise ValueError(
            f"the home and away rates must have the same length, "
            f"got {rate_home.size} and {rate_away.size}"
        )
    goals = np.arange(MAX_GOALS + 1, dtype=np.float64)
    grid_home = goals[None, :, None]
    grid_away = goals[None, None, :]
    home_rates = rate_home[:, None, None]
    away_rates = rate_away[:, None, None]
    independent = np.asarray(poisson.pmf(grid_home, home_rates), dtype=np.float64) * np.asarray(
        poisson.pmf(grid_away, away_rates), dtype=np.float64
    )
    tau = tau_correction(grid_home, grid_away, home_rates, away_rates, rho)
    matrix = np.maximum(independent * tau, 0.0)
    totals = matrix.sum(axis=(1, 2), keepdims=True)
    if not bool((totals > 0.0).all()):
        raise ValueError(
            "a score matrix has no positive mass on the 0..10 grid: both goal "
            "rates are so large that every Poisson pmf underflows to zero"
        )
    return matrix / totals


_score_matrix_from_rates = score_matrix
"""Private alias of :func:`score_matrix`, for the model method of the same name.

Inside :meth:`DixonColesModel.score_matrix` the bare name refers to the method,
not to the module function, so the method reaches the function through this
alias. The public function keeps its name and signature.
"""


def negative_log_likelihood(
    theta: NDArray[np.float64], history: History, cutoff: datetime.date
) -> tuple[float, NDArray[np.float64]]:
    """Return the penalized weighted NLL of ``theta`` and its analytic gradient.

    The window, the team index mapping and the weights are rebuilt from
    ``history`` and ``cutoff`` on every call, so the value and the gradient are
    those of the objective :class:`DixonColesModel` minimizes. Both rates are
    exponentiated through :func:`_clipped_exp`, so a finite but extreme ``theta``
    gives a huge, finite objective and gradient instead of ``inf`` and ``nan``.

    Args:
        theta: The parameter vector in the layout of the module docstring.
        history: Played matches; only the window ``[cutoff - 1095 days, cutoff)``
            is used.
        cutoff: The fit boundary, exclusive for history.

    Returns:
        The objective value and the gradient with respect to ``theta``.

    Raises:
        ValueError: ``theta`` does not have length ``3 + 2 * K``.
    """
    design = _window_design(history, cutoff)
    team_count = int(design.team_ids.size)
    expected = 3 + 2 * team_count
    if theta.size != expected:
        raise ValueError(f"theta must have {expected} entries for {team_count} teams")
    mu = float(theta[0])
    gamma = float(theta[1])
    alpha = theta[2 : 2 + team_count]
    beta = theta[2 + team_count : 2 + 2 * team_count]
    rho = float(theta[-1])

    home_goals = design.home_goals
    away_goals = design.away_goals
    weights = design.weights
    eta_home = mu + gamma + alpha[design.home_index] - beta[design.away_index]
    eta_away = mu + alpha[design.away_index] - beta[design.home_index]
    rate_home = _clipped_exp(eta_home)
    rate_away = _clipped_exp(eta_away)

    tau = tau_correction(home_goals, away_goals, rate_home, rate_away, rho)
    usable = tau >= _TAU_FLOOR
    safe_tau = np.where(usable, tau, _TAU_FLOOR)
    log_tau = np.log(safe_tau)
    log_likelihood = home_goals * eta_home - rate_home + away_goals * eta_away - rate_away + log_tau
    penalty = L2_PENALTY * float(np.sum(alpha * alpha) + np.sum(beta * beta))
    objective = -float(np.sum(weights * log_likelihood)) + penalty

    cell_00 = (home_goals == 0) & (away_goals == 0)
    cell_01 = (home_goals == 0) & (away_goals == 1)
    cell_10 = (home_goals == 1) & (away_goals == 0)
    cell_11 = (home_goals == 1) & (away_goals == 1)
    d_log_home = np.where(cell_00, -rate_away * rho, 0.0) + np.where(cell_01, rho, 0.0)
    d_log_away = np.where(cell_00, -rate_home * rho, 0.0) + np.where(cell_10, rho, 0.0)
    d_log_rho = (
        np.where(cell_00, -rate_home * rate_away, 0.0)
        + np.where(cell_01, rate_home, 0.0)
        + np.where(cell_10, rate_away, 0.0)
        + np.where(cell_11, -1.0, 0.0)
    )
    d_log_home = np.where(usable, d_log_home / safe_tau, 0.0)
    d_log_away = np.where(usable, d_log_away / safe_tau, 0.0)
    d_log_rho = np.where(usable, d_log_rho / safe_tau, 0.0)

    home_part = weights * (rate_home - home_goals - rate_home * d_log_home)
    away_part = weights * (rate_away - away_goals - rate_away * d_log_away)
    gradient = np.zeros_like(theta)
    gradient[0] = float(home_part.sum() + away_part.sum())
    gradient[1] = float(home_part.sum())
    alpha_gradient = np.bincount(
        design.home_index, weights=home_part, minlength=team_count
    ) + np.bincount(design.away_index, weights=away_part, minlength=team_count)
    beta_gradient = -(
        np.bincount(design.home_index, weights=away_part, minlength=team_count)
        + np.bincount(design.away_index, weights=home_part, minlength=team_count)
    )
    gradient[2 : 2 + team_count] = alpha_gradient + 2.0 * L2_PENALTY * alpha
    gradient[2 + team_count : 2 + 2 * team_count] = beta_gradient + 2.0 * L2_PENALTY * beta
    gradient[-1] = -float(np.sum(weights * d_log_rho))
    return objective, gradient


class DixonColesModel(Model):
    """The Dixon-Coles goal model, fitted on the three years before the cutoff.

    Attributes:
        parameters: The fitted :class:`DixonColesParameters`, or ``None`` before
            :meth:`fit` succeeds.
    """

    name = "dixon_coles"
    version = "1"

    def __init__(self, *, seed: int) -> None:
        super().__init__(seed=seed)
        self.parameters: DixonColesParameters | None = None
        self._newcomer_alpha = 0.0
        self._newcomer_beta = 0.0

    @property
    def hyperparameters(self) -> Mapping[str, float | int]:
        """The fixed values: decay, window, penalty, bound, grid and seed."""
        return {
            "seed": self.seed,
            "time_decay_xi": TIME_DECAY_XI,
            "window_days": WINDOW_DAYS,
            "l2_penalty": L2_PENALTY,
            "rho_bound": RHO_BOUND,
            "max_goals": MAX_GOALS,
        }

    def _fit(self, history: History, cutoff: datetime.date) -> None:
        """Estimate the parameters on the window, or fall back to zeros when empty.

        An empty window is not a fit failure: it returns the zero parameters and
        zero newcomer strengths.

        Usable parameters are the hard requirement: the returned vector must have
        the documented length and every entry must be finite, or
        :class:`ModelFitError` is raised instead of a replaced answer. A
        ``success=False`` status with usable finite parameters is only a warning,
        because L-BFGS-B reports it (``ABNORMAL_TERMINATION_IN_LNSRCH``, for
        example) with perfectly usable parameters and the measured count on real
        data is zero.

        Raises:
            ModelFitError: the optimizer returned a vector of the wrong length or
                with an entry that is not finite.
        """
        design = _window_design(history, cutoff)
        team_count = int(design.team_ids.size)
        if design.match_count == 0:
            self.parameters = DixonColesParameters(
                mu=0.0,
                gamma=0.0,
                rho=0.0,
                team_ids=design.team_ids,
                alpha=np.zeros(0, dtype=np.float64),
                beta=np.zeros(0, dtype=np.float64),
            )
            self._newcomer_alpha = 0.0
            self._newcomer_beta = 0.0
            return

        theta0 = _initial_theta(design)

        def objective(theta: NDArray[np.float64]) -> float:
            return negative_log_likelihood(theta, history, cutoff)[0]

        def gradient(theta: NDArray[np.float64]) -> NDArray[np.float64]:
            return negative_log_likelihood(theta, history, cutoff)[1]

        result = minimize(
            objective,
            theta0,
            jac=gradient,
            method="L-BFGS-B",
            bounds=_parameter_bounds(team_count),
        )
        theta = np.asarray(result.x, dtype=np.float64).reshape(-1)
        trail = f"optimizer status {result.status}, message {str(result.message)!r}"
        if theta.shape != theta0.shape:
            raise ModelFitError(
                f"{self.name}: the optimizer returned {theta.size} parameters where "
                f"{theta0.size} are expected (shape check); {trail}"
            )
        not_finite = int(theta.size - np.count_nonzero(np.isfinite(theta)))
        if not_finite:
            raise ModelFitError(
                f"{self.name}: the optimizer returned {not_finite} of {theta.size} parameters "
                f"that are not finite; {trail}"
            )
        if not bool(result.success):
            logger.warning(
                "%s: the optimizer reported success=False (status %s: %s) but its parameters "
                "are finite and usable; using them",
                self.name,
                result.status,
                result.message,
            )
        alpha = theta[2 : 2 + team_count].copy()
        beta = theta[2 + team_count : 2 + 2 * team_count].copy()
        self.parameters = DixonColesParameters(
            mu=float(theta[0]),
            gamma=float(theta[1]),
            rho=float(theta[-1]),
            team_ids=design.team_ids,
            alpha=alpha,
            beta=beta,
        )
        self._newcomer_alpha, self._newcomer_beta = _newcomer_strengths(design, alpha, beta)

    def score_matrix(self, fixtures: Fixtures) -> NDArray[np.float64]:
        """Return the renormalized (n, 11, 11) score matrix of each fixture.

        Entry ``[i, h, a]`` is the probability of the score ``h``-``a`` in fixture
        ``i``, renormalized like the module function :func:`score_matrix`, which
        shares its name. The input guards are the ones :meth:`predict` applies.

        Raises:
            RuntimeError: :meth:`fit` has not been called yet.
            LeakageError: a ``History`` was passed instead of fixtures, or a
                fixture is dated before the cutoff the model was fitted at.
            ValueError: a fixture's goal rates are so large that the ``0..10``
                grid holds no positive mass; see :func:`score_matrix`.
        """
        self._guard_fixtures(fixtures)
        parameters = self.parameters
        if parameters is None:
            raise RuntimeError(f"{self.name}: predict() called before fit()")
        home_alpha, home_beta = _team_strengths(
            parameters, fixtures.home_team_id, self._newcomer_alpha, self._newcomer_beta
        )
        away_alpha, away_beta = _team_strengths(
            parameters, fixtures.away_team_id, self._newcomer_alpha, self._newcomer_beta
        )
        rate_home = _clipped_exp(parameters.mu + parameters.gamma + home_alpha - away_beta)
        rate_away = _clipped_exp(parameters.mu + away_alpha - home_beta)
        return _score_matrix_from_rates(rate_home, rate_away, parameters.rho)

    def _predict(self, fixtures: Fixtures) -> NDArray[np.float64]:
        """Return the H/D/A probabilities of each fixture from the score matrix."""
        matrix = self.score_matrix(fixtures)
        probabilities = np.empty((len(fixtures), 3), dtype=np.float64)
        probabilities[:, _HOME] = np.tril(matrix, -1).sum(axis=(1, 2))
        probabilities[:, _DRAW] = np.diagonal(matrix, axis1=1, axis2=2).sum(axis=1)
        probabilities[:, _AWAY] = np.triu(matrix, 1).sum(axis=(1, 2))
        return probabilities


@dataclass(frozen=True)
class _WindowDesign:
    """The rows of the fitting window, with the design matrices the fit needs."""

    team_ids: NDArray[np.int64]
    home_index: NDArray[np.intp]
    away_index: NDArray[np.intp]
    home_goals: NDArray[np.float64]
    away_goals: NDArray[np.float64]
    weights: NDArray[np.float64]
    season_ids: NDArray[np.int64]

    @property
    def match_count(self) -> int:
        """Return the number of matches in the window."""
        return int(self.home_goals.size)

    @property
    def team_count(self) -> int:
        """Return the number of distinct teams in the window."""
        return int(self.team_ids.size)


def _window_design(history: History, cutoff: datetime.date) -> _WindowDesign:
    """Return the window rows of ``history`` and their weights and team indices."""
    cutoff_day = np.datetime64(cutoff, "D")
    start_day = cutoff_day - np.timedelta64(WINDOW_DAYS, "D")
    in_window = (history.kickoff_date >= start_day) & (history.kickoff_date < cutoff_day)
    home_teams = history.home_team_id[in_window]
    away_teams = history.away_team_id[in_window]
    team_ids = np.unique(np.concatenate([home_teams, away_teams]))
    days = (cutoff_day - history.kickoff_date[in_window]).astype(np.int64)
    return _WindowDesign(
        team_ids=team_ids,
        home_index=np.searchsorted(team_ids, home_teams).astype(np.intp),
        away_index=np.searchsorted(team_ids, away_teams).astype(np.intp),
        home_goals=history.home_goals[in_window].astype(np.float64),
        away_goals=history.away_goals[in_window].astype(np.float64),
        weights=time_weight(days),
        season_ids=history.season_id[in_window],
    )


def _initial_theta(design: _WindowDesign) -> NDArray[np.float64]:
    """Return the fixed initial vector: zero strengths and the average goal rate."""
    team_count = design.team_count
    theta = np.zeros(3 + 2 * team_count, dtype=np.float64)
    total_goals = float(np.sum(design.home_goals) + np.sum(design.away_goals))
    rate = max(total_goals / (2.0 * design.match_count), _MIN_INITIAL_RATE)
    theta[0] = float(np.log(rate))
    return theta


def _parameter_bounds(team_count: int) -> Bounds:
    """Return the box: ``rho`` bounded, every other parameter free."""
    lower = np.full(3 + 2 * team_count, -np.inf, dtype=np.float64)
    upper = np.full(3 + 2 * team_count, np.inf, dtype=np.float64)
    lower[-1] = -RHO_BOUND
    upper[-1] = RHO_BOUND
    return Bounds(lb=lower, ub=upper)


def _newcomer_strengths(
    design: _WindowDesign, alpha: NDArray[np.float64], beta: NDArray[np.float64]
) -> tuple[float, float]:
    """Return the strengths a team absent from the window inherits."""
    if design.match_count == 0 or design.team_count == 0:
        return 0.0, 0.0
    recent_season = design.season_ids.max()
    played_recently = np.zeros(design.team_count, dtype=bool)
    recent_rows = design.season_ids == recent_season
    played_recently[design.home_index[recent_rows]] = True
    played_recently[design.away_index[recent_rows]] = True
    candidates = np.flatnonzero(played_recently)
    if candidates.size == 0:  # every team of the window is a candidate in practice
        candidates = np.arange(design.team_count, dtype=np.intp)
    net = alpha - beta
    order = candidates[np.lexsort((design.team_ids[candidates], net[candidates]))]
    chosen = order[: min(_NEWCOMER_TEAMS, order.size)]
    return float(np.mean(alpha[chosen])), float(np.mean(beta[chosen]))


def _team_strengths(
    parameters: DixonColesParameters,
    team_ids: NDArray[np.int64],
    newcomer_alpha: float,
    newcomer_beta: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Return the attack and defence strength of each team id, newcomers included."""
    if parameters.team_ids.size == 0:
        return (
            np.full(team_ids.size, newcomer_alpha, dtype=np.float64),
            np.full(team_ids.size, newcomer_beta, dtype=np.float64),
        )
    index = np.clip(np.searchsorted(parameters.team_ids, team_ids), 0, parameters.team_ids.size - 1)
    known = parameters.team_ids[index] == team_ids
    alpha = np.where(known, parameters.alpha[index], newcomer_alpha)
    beta = np.where(known, parameters.beta[index], newcomer_beta)
    return alpha, beta


def _clipped_exp(eta: NDArray[np.float64]) -> NDArray[np.float64]:
    """Return ``exp(eta)`` with the argument clipped at :data:`_MAX_EXP_ARGUMENT`.

    An extreme but finite parameter vector would otherwise overflow to ``inf``,
    and an ``inf`` rate turns the objective and the gradient into ``inf`` and
    ``nan``. The clip never binds inside the range a fit reaches, so no fitted
    number moves. Beyond the clip the analytic gradient is no longer the
    derivative of the clipped objective; it only keeps the values finite there.
    """
    return np.exp(np.minimum(eta, _MAX_EXP_ARGUMENT))


def _as_rates(values: ArrayLike) -> NDArray[np.float64]:
    """Return ``values`` as a one-dimensional float array, treating a scalar as one row."""
    rates = np.atleast_1d(np.asarray(values, dtype=np.float64))
    if rates.ndim != 1:
        raise ValueError(f"goal rates must be one dimensional, got shape {rates.shape}")
    return rates
