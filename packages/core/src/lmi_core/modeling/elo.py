"""The Elo model: a rating replay, and an ordered logit on the rating gap.

Two layers sit in this module and neither reads the other's future:

* the **ratings** come from a replay of the history in stored order, one kickoff
  date at a time. The pre-match gap ``d = (R_home + H - R_away) / 400`` is
  recorded before the date's matches move a rating, then the two ratings of a
  match move by ``K * (S - E)`` with ``E`` the logistic expected score of that gap
  and ``S`` the outcome (1 home win, 0.5 draw, 0 away win). The data carries a
  kickoff date and no kickoff time, so the row order inside a date is not evidence
  that one result was known before another match of the same date: every match of
  a date is priced from the state the date starts in, and the date's results are
  applied afterwards. A match therefore never sees its own result or a result of
  the same date, so every ``d`` is a genuine forecast input.
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
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize
from scipy.special import expit

from lmi_core.modeling.base import Model
from lmi_core.modeling.types import OUTCOMES, Fixtures, History

logger = logging.getLogger(__name__)
"""Logs the one optimizer status that is a warning rather than a fit error."""

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


class ModelFitError(RuntimeError):
    """The fit failure of every fitted model: the optimizer returned unusable parameters.

    It lives here because it is raised by every fitted model and this module is the
    lighter of the two, so the goal model imports it from here and not the other way
    around.
    """


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
    """Home rating the match was priced from: the state its kickoff date starts in."""
    away_rating: float
    """Away rating the match was priced from: the state its kickoff date starts in."""
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
    fit never depends on a previous one. An empty input is not optimized at all:
    it returns the fixed start, which is a fit of nothing and not a failure.

    Usable parameters are the hard requirement. The optimizer's vector must carry
    exactly the three free parameters, every entry must be finite, and the logit
    built from them must keep ``b`` and ``c2`` finite, because a finite ``log_b``
    above about 709 still overflows ``exp``. A vector that fails any of these is
    not a fit: :class:`ModelFitError` is raised instead of a replaced answer.

    A non-success status is only a warning. L-BFGS-B reports ``success=False``
    (``ABNORMAL_TERMINATION_IN_LNSRCH``, for example) with perfectly usable finite
    parameters, and the measured count on real data is zero, so such a fit logs
    one warning and its parameters are used; the numeric check above stays the one
    that fails a fit.

    Args:
        d: Pre-match gaps, shape ``(n,)``, in history order.
        result: ``OUTCOMES`` index of each match, shape ``(n,)``.

    Returns:
        The fitted parameters, or the fixed start for an empty input.

    Raises:
        ModelFitError: the optimizer returned a vector of the wrong length, an
            entry that is not finite, or a ``log_b`` or ``log_gap`` so large that
            ``b`` or ``c2`` overflows ``exp``.
    """
    gaps = np.asarray(d, dtype=np.float64).reshape(-1)
    outcomes = np.asarray(result, dtype=np.int8).reshape(-1)
    if gaps.size == 0:
        return _default_params()
    start = _default_params()
    solved = minimize(
        _negative_log_likelihood, start.as_vector(), args=(gaps, outcomes), method="L-BFGS-B"
    )
    trail = _optimizer_trail(solved.status, solved.message)
    solution = np.asarray(solved.x, dtype=np.float64).reshape(-1)
    if solution.shape != (3,):
        raise ModelFitError(
            f"elo: the optimizer returned {solution.size} parameters where 3 are expected "
            f"(shape check); {trail}"
        )
    not_finite = int(solution.size - np.count_nonzero(np.isfinite(solution)))
    if not_finite:
        raise ModelFitError(
            f"elo: the optimizer returned {not_finite} of {solution.size} parameters that "
            f"are not finite; {trail}"
        )
    params = LogitParams(
        log_b=float(solution[0]),
        c1=float(solution[1]),
        log_gap=float(solution[2]),
    )
    unusable = [
        name for name, value in (("b", params.b), ("c2", params.c2)) if not math.isfinite(value)
    ]
    if unusable:
        raise ModelFitError(
            f"elo: the fitted logit is unusable because exp overflows at the optimum "
            f"({', '.join(unusable)} not finite, logit check); {trail}"
        )
    if not bool(solved.success):
        logger.warning(
            "elo: the optimizer reported success=False (status %s: %s) but its parameters "
            "are finite and usable; using them",
            solved.status,
            solved.message,
        )
    return params


def _optimizer_trail(status: object, message: object) -> str:
    """Return the optimizer's status and message, for a fit-failure message."""
    return f"optimizer status {status}, message {str(message)!r}"


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
        """Replay ``history`` one kickoff date at a time, then fit the logit on its gaps."""
        self.ratings_ = {}
        self.initial_ratings_ = {}
        self.newcomer_rating_ = START_RATING
        self.d_values_ = np.empty(0, dtype=np.float64)
        self.replay_ = ()
        self.params_ = _default_params()
        self._candidates = _newest_season_teams(history)

        steps: list[RatingStep] = []
        gaps: list[float] = []
        for rows in _by_kickoff_date(history):
            # Everything a date's matches see comes from the state the date starts
            # in: every newcomer of the date is assigned from that one snapshot, so
            # neither a team's listing side nor another result of the same date can
            # change the rating it starts from, and no gap of the date can contain
            # a result of the same date.
            opening = dict(self.ratings_)
            for row in rows:
                for team_id in (
                    int(history.home_team_id[row]),
                    int(history.away_team_id[row]),
                ):
                    if team_id in opening:
                        continue
                    # Resolved against the date's opening state, never against a
                    # value another match of the same date assigned.
                    assigned = newcomer_value(self.ratings_, self._candidates)
                    opening[team_id] = assigned
                    self.initial_ratings_[team_id] = assigned
            date_gaps: list[float] = []
            for row in rows:
                home_team = int(history.home_team_id[row])
                away_team = int(history.away_team_id[row])
                gap = (opening[home_team] + HOME_ADVANTAGE - opening[away_team]) / RATING_SCALE
                steps.append(
                    RatingStep(
                        match_id=int(history.match_id[row]),
                        home_team_id=home_team,
                        away_team_id=away_team,
                        home_rating=opening[home_team],
                        away_rating=opening[away_team],
                        d=gap,
                    )
                )
                date_gaps.append(gap)
            gaps.extend(date_gaps)
            self.ratings_ = opening
            for row, gap in zip(rows, date_gaps, strict=True):
                home_team = int(history.home_team_id[row])
                away_team = int(history.away_team_id[row])
                score = float(_HOME_SCORE[int(history.result[row])])
                shift = K_FACTOR * (score - float(expected_score(gap).item()))
                self.ratings_[home_team] = self.ratings_[home_team] + shift
                self.ratings_[away_team] = self.ratings_[away_team] - shift

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


def _by_kickoff_date(history: History) -> list[list[int]]:
    """Return the row indices of ``history`` grouped by kickoff date, in stored order.

    The date is the finest granularity the data carries, so it is the finest
    granularity at which a result can be said to be known.
    """
    groups: list[list[int]] = []
    previous: np.datetime64 | None = None
    for row in range(len(history)):
        kickoff = history.kickoff_date[row]
        if previous is None or kickoff != previous:
            groups.append([])
            previous = kickoff
        groups[-1].append(row)
    return groups


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
