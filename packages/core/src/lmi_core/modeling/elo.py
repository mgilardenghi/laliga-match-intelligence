"""The Elo model: a rating replay, and an ordered logit on the rating gap.

Two layers sit in this module and neither reads the other's future:

* the **ratings** come from a replay of the history in stored order. Before each
  match the pre-match gap ``d = (R_home + H - R_away) / 400`` is recorded, then
  the two ratings move by ``K * (S - E)`` with ``E`` the logistic expected score
  of that gap and ``S`` the outcome (1 home win, 0.5 draw, 0 away win). A match
  never sees its own result, so every ``d`` is a genuine forecast input.
* the **ordered logit** maps a gap to the three outcomes with two cut points,
  ``P(A) = sigma(c1 - b d)`` and ``P(A or D) = sigma(c2 - b d)``. The draw is the
  difference of the two, so the three probabilities sum to 1 by construction.

The model is deterministic: the seed is recorded in the hyperparameters and
never used, so no draw happens anywhere. The hyperparameters are fixed a priori
and not tuned, which is why a walk-forward run can start every window from the
same constants instead of carrying state forward.

The replay exposes its own trail (:attr:`EloModel.replay_`) and so does the
likelihood (:attr:`EloModel.d_values_`), so a test can check the fitted gaps are
the pre-match ones without reading the implementation.
"""

import datetime
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize
from scipy.special import expit

from lmi_core.modeling.base import Model
from lmi_core.modeling.types import OUTCOMES, Fixtures, History

START_RATING = 1500.0
"""Rating a team starts from when no rated team exists to learn one from."""

K_FACTOR = 20.0
"""Elo K factor: the number of points a certain win moves the rating."""

HOME_ADVANTAGE = 60.0
"""Points added to the home rating when the gap is computed."""

RATING_SCALE = 400.0
"""Rating points per unit of ``d``, the conventional Elo scale."""

NEWCOMER_SAMPLE = 3
"""How many of the lowest current ratings a newcomer is averaged over."""

_HOME = 0
_DRAW = 1
_AWAY = 2
_LOG_10 = math.log(10.0)
_HOME_SCORE = np.array([1.0, 0.5, 0.0], dtype=np.float64)

_START_LOG_B = 0.0
_START_C1 = -0.8
_START_LOG_GAP = math.log(1.9)
"""Fixed start of the likelihood: ``b = 1.0`` and ``c2 = 1.1``."""

_LOG_GAP_FLOOR = -1e-12
"""The log of the draw mass is never taken at exactly zero, only near it."""

_MAX_EXP_ARGUMENT = 700.0
_WORST_NLL = 1e300
"""Penalty for a parameter vector whose likelihood underflows to zero."""


@dataclass(frozen=True)
class LogitParams:
    """The ordered logit, parametrized so the cut points stay ordered.

    ``b = exp(log_b)`` is positive and ``c2 = c1 + exp(log_gap)`` is above ``c1``
    for every real value of the three free parameters, so the optimizer cannot
    walk into a region where ``P(A or D) < P(A)``.
    """

    log_b: float
    c1: float
    log_gap: float

    @property
    def b(self) -> float:
        """Slope of the gap: how fast the outcome odds move with ``d``."""
        return math.exp(self.log_b) if self.log_b < _MAX_EXP_ARGUMENT else math.inf

    @property
    def c2(self) -> float:
        """Second cut point; strictly above :attr:`c1`."""
        return self.c1 + (math.exp(self.log_gap) if self.log_gap < _MAX_EXP_ARGUMENT else math.inf)

    def as_vector(self) -> NDArray[np.float64]:
        """Return the free parameters in optimizer order."""
        return np.array([self.log_b, self.c1, self.log_gap], dtype=np.float64)


def _default_params() -> LogitParams:
    """Return the fixed start of every fit, never a carried-over value."""
    return LogitParams(log_b=_START_LOG_B, c1=_START_C1, log_gap=_START_LOG_GAP)


@dataclass(frozen=True)
class RatingStep:
    """One match of the replay, with the ratings and the gap it was played at."""

    match_id: int
    home_team_id: int
    away_team_id: int
    home_rating: float
    """Home rating *before* this match moved it."""
    away_rating: float
    """Away rating *before* this match moved it."""
    d: float
    """``(home_rating + HOME_ADVANTAGE - away_rating) / RATING_SCALE``."""


def expected_score(d: float | NDArray[np.float64]) -> NDArray[np.float64]:
    """Return the home side's expected score ``E = 1 / (1 + 10 ** -d)``.

    Written as ``expit(d * ln 10)``: the same value in a form that saturates
    instead of overflowing for a large ``d``. The result keeps the input shape,
    so a scalar ``d`` gives a 0-d array.

    Args:
        d: The rating gap in units of ``RATING_SCALE`` points.
    """
    scaled = np.asarray(d, dtype=np.float64) * _LOG_10
    return np.asarray(expit(scaled), dtype=np.float64)


def newcomer_value(ratings: Mapping[int, float], candidates: Sequence[int]) -> float:
    """Return the rating a team without one gets.

    The value is the mean of the :data:`NEWCOMER_SAMPLE` lowest current ratings
    among ``candidates`` that already have a rating: the weakest teams of the
    season the newcomer enters. With fewer than three rated candidates it is the
    mean of those, and with none it is :data:`START_RATING`.

    Args:
        ratings: Current rating per team id.
        candidates: Team ids of the newest season in the fitted history.

    Returns:
        The newcomer rating, never above the highest rated candidate.
    """
    rated = sorted(ratings[team_id] for team_id in candidates if team_id in ratings)
    if not rated:
        return START_RATING
    return float(np.mean(rated[:NEWCOMER_SAMPLE]))


def outcome_probabilities(d: NDArray[np.float64], params: LogitParams) -> NDArray[np.float64]:
    """Return one H/D/A probability row per gap.

    Args:
        d: The rating gaps, shape ``(n,)``.
        params: The fitted ordered logit.

    Returns:
        Probabilities in ``OUTCOMES`` order, shape ``(n, 3)``; each row sums to
        1 because the draw is the difference of the two nested sigmoids.
    """
    gaps = np.asarray(d, dtype=np.float64).reshape(-1)
    shifted = params.b * gaps
    p_away = _sigmoid(params.c1 - shifted)
    p_away_or_draw = _sigmoid(params.c2 - shifted)
    p_home = 1.0 - p_away_or_draw
    p_draw = p_away_or_draw - p_away
    return np.stack((p_home, p_draw, p_away), axis=1)


def fit_params(d: NDArray[np.float64], result: NDArray[np.int8]) -> LogitParams:
    """Fit the ordered logit on the pre-match gaps and their outcomes.

    Every call starts from the fixed constants in :func:`_default_params`, so a
    fit never depends on a previous one. An empty input is not optimized at all.

    Args:
        d: Pre-match gaps, shape ``(n,)``, in history order.
        result: ``OUTCOMES`` index of each match, shape ``(n,)``.

    Returns:
        The fitted parameters, or the fixed start when the solutions are not all
        finite. A non-success status is not raised: an unconverged but finite
        point is still a usable forecast, and the walk-forward report shows its
        cost.
    """
    gaps = np.asarray(d, dtype=np.float64).reshape(-1)
    outcomes = np.asarray(result, dtype=np.int8).reshape(-1)
    if gaps.size == 0:
        return _default_params()
    start = _default_params()
    solved = minimize(
        _negative_log_likelihood, start.as_vector(), args=(gaps, outcomes), method="L-BFGS-B"
    )
    solution = np.asarray(solved.x, dtype=np.float64).reshape(-1)
    if solution.shape != (3,) or not bool(np.all(np.isfinite(solution))):
        return start
    return LogitParams(
        log_b=float(solution[0]),
        c1=float(solution[1]),
        log_gap=float(solution[2]),
    )


class EloModel(Model):
    """Elo ratings plus an ordered logit on the pre-match rating gap.

    The replay and the likelihood are fitted from scratch on every ``fit``: no
    rating, gap or parameter survives a window, so a walk-forward run cannot
    carry information forward by accident.
    """

    name = "elo"
    version = "1"

    def __init__(self, *, seed: int) -> None:
        super().__init__(seed=seed)
        self.ratings_: dict[int, float] = {}
        """Current rating per team, after the replay."""
        self.initial_ratings_: dict[int, float] = {}
        """The rating each team was first assigned, newcomer value included."""
        self.newcomer_rating_: float = START_RATING
        """The rating a team with no rating gets at predict time."""
        self.d_values_: NDArray[np.float64] = np.empty(0, dtype=np.float64)
        """The pre-match gap of every replayed match, in history order."""
        self.replay_: tuple[RatingStep, ...] = ()
        """The replay trail: one step per replayed match."""
        self.params_: LogitParams = _default_params()
        """The fitted ordered logit."""
        self._candidates: tuple[int, ...] = ()
        """Team ids of the newest season in the fitted history."""

    @property
    def hyperparameters(self) -> Mapping[str, object]:
        """The fixed values recorded next to each stored prediction."""
        return {
            "seed": self.seed,
            "start_rating": START_RATING,
            "k_factor": K_FACTOR,
            "home_advantage": HOME_ADVANTAGE,
            "rating_scale": RATING_SCALE,
            "newcomer_sample": NEWCOMER_SAMPLE,
        }

    def _fit(self, history: History, cutoff: datetime.date) -> None:
        """Replay ``history`` in stored order, then fit the logit on its gaps."""
        self.ratings_ = {}
        self.initial_ratings_ = {}
        self.newcomer_rating_ = START_RATING
        self.d_values_ = np.empty(0, dtype=np.float64)
        self.replay_ = ()
        self.params_ = _default_params()
        self._candidates = _newest_season_teams(history)

        steps: list[RatingStep] = []
        gaps: list[float] = []
        for row in range(len(history)):
            home_team = int(history.home_team_id[row])
            away_team = int(history.away_team_id[row])
            home_rating = self._rating_before(home_team)
            away_rating = self._rating_before(away_team)
            gap = (home_rating + HOME_ADVANTAGE - away_rating) / RATING_SCALE
            steps.append(
                RatingStep(
                    match_id=int(history.match_id[row]),
                    home_team_id=home_team,
                    away_team_id=away_team,
                    home_rating=home_rating,
                    away_rating=away_rating,
                    d=gap,
                )
            )
            gaps.append(gap)
            score = float(_HOME_SCORE[int(history.result[row])])
            shift = K_FACTOR * (score - float(expected_score(gap).item()))
            self.ratings_[home_team] = home_rating + shift
            self.ratings_[away_team] = away_rating - shift

        self.replay_ = tuple(steps)
        self.d_values_ = np.array(gaps, dtype=np.float64)
        self.params_ = fit_params(self.d_values_, history.result)
        self.newcomer_rating_ = newcomer_value(self.ratings_, self._candidates)

    def _predict(self, fixtures: Fixtures) -> NDArray[np.float64]:
        """Return the fitted ordered logit of each fixture's pre-match gap."""
        n = len(fixtures)
        if n == 0:
            return np.empty((0, len(OUTCOMES)), dtype=np.float64)
        gaps = np.empty(n, dtype=np.float64)
        for row in range(n):
            home_rating = self.ratings_.get(int(fixtures.home_team_id[row]), self.newcomer_rating_)
            away_rating = self.ratings_.get(int(fixtures.away_team_id[row]), self.newcomer_rating_)
            gaps[row] = (home_rating + HOME_ADVANTAGE - away_rating) / RATING_SCALE
        return outcome_probabilities(gaps, self.params_)

    def _rating_before(self, team_id: int) -> float:
        """Return a team's rating, assigning the newcomer value on its debut."""
        rating = self.ratings_.get(team_id)
        if rating is not None:
            return rating
        assigned = newcomer_value(self.ratings_, self._candidates)
        self.ratings_[team_id] = assigned
        self.initial_ratings_[team_id] = assigned
        return assigned


def _newest_season_teams(history: History) -> tuple[int, ...]:
    """Return the team ids playing the newest season present in ``history``."""
    if len(history) == 0:
        return ()
    newest = int(history.season_id.max())
    in_newest = history.season_id == newest
    teams = np.unique(
        np.concatenate((history.home_team_id[in_newest], history.away_team_id[in_newest]))
    )
    return tuple(int(team_id) for team_id in teams)


def _sigmoid(x: NDArray[np.float64]) -> NDArray[np.float64]:
    """Return ``sigma(x)`` as ``float64`` without overflowing."""
    return np.asarray(expit(np.asarray(x, dtype=np.float64)), dtype=np.float64)


def _log_sigmoid(x: NDArray[np.float64]) -> NDArray[np.float64]:
    """Return ``log sigma(x) = -logaddexp(0, -x)``, never ``log(0)``."""
    return -np.logaddexp(0.0, -np.asarray(x, dtype=np.float64))


def _negative_log_likelihood(
    theta: NDArray[np.float64], d: NDArray[np.float64], result: NDArray[np.int8]
) -> float:
    """Return the negative log-likelihood of the ordered logit at ``theta``.

    ``theta`` is ``(log_b, c1, log_gap)``, so the cut points stay ordered and the
    draw probability is a difference of two nested sigmoids. The difference is
    clamped just below 1 before its log is taken: two sigmoids that are equal in
    floating point would otherwise read as a draw mass of exactly zero.
    """
    log_b, c1, log_gap = float(theta[0]), float(theta[1]), float(theta[2])
    shifted = _bounded_exp(log_b) * d
    c2 = c1 + _bounded_exp(log_gap)
    log_away = _log_sigmoid(c1 - shifted)
    log_away_or_draw = _log_sigmoid(c2 - shifted)
    log_home = _log_sigmoid(shifted - c2)
    ratio = np.minimum(log_away - log_away_or_draw, _LOG_GAP_FLOOR)
    log_draw = log_away_or_draw + np.log(-np.expm1(ratio))
    chosen = np.where(
        result == _HOME,
        log_home,
        np.where(result == _DRAW, log_draw, log_away),
    )
    total = -float(np.sum(chosen))
    return total if math.isfinite(total) else _WORST_NLL


def _bounded_exp(value: float) -> float:
    """Return ``exp(value)``, or ``inf`` instead of raising on an overflow."""
    return math.exp(value) if value < _MAX_EXP_ARGUMENT else math.inf
