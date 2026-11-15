"""Synthetic data and leakage assertions shared by every model test.

Everything here is synthetic: team ids are integers, the bookmaker label is a
placeholder, and no real club, league or odds file appears. The league is
derived from ``seed`` alone, so the assertions are reproducible.

The module offers two kinds of helper:

* builders -- :func:`make_synthetic_league` (a double round robin over several
  seasons), :func:`make_history` (a short history from a list of results) and
  :func:`make_fixtures`;
* leakage assertions -- :func:`assert_fit_predict_invariant`, which every model
  test calls for its own model. :func:`fit_and_predict`,
  :func:`history_before`, :func:`fixtures_between` and
  :func:`perturb_results_from` are the seams a walk-forward invariance
  assertion is built from.
"""

import datetime
from collections.abc import Sequence
from typing import Final

import numpy as np
from lmi_core.evaluation.walkforward import walk_forward, weekly_cutoffs
from lmi_core.modeling.registry import make_model
from lmi_core.modeling.types import Fixtures, History, MatchData
from numpy.typing import NDArray

DEFAULT_SEED: Final = 7
"""Seed of the synthetic league and of the models these assertions construct."""

_FIRST_SEASON_YEAR: Final = 2005
_SEASON_START_MONTH: Final = 8
_SEASON_START_DAY: Final = 6
_ROUND_STEP_DAYS: Final = 7
_WINDOW_DAYS: Final = 7
_GOALS_CAP: Final = 6
_BOOKMAKER: Final = "synthetic"
_ODDS_MARGIN: Final = 1.05
_RESULT_COUNT: Final = 3

# A canonical scoreline per result index, used by the perturbations.
_SCORE_FOR_RESULT: Final = ((1, 0), (1, 1), (0, 1))

_DEFAULT_HISTORY_DATE: Final = datetime.date(2020, 1, 4)
_DEFAULT_FIXTURE_DATE: Final = datetime.date(2021, 1, 16)


def make_synthetic_league(
    n_teams: int = 10, n_seasons: int = 3, seed: int = DEFAULT_SEED
) -> MatchData:
    """Return a synthetic league: a double round robin on weekend matchdays.

    Each season has ``n_teams * (n_teams - 1)`` matches, one round per week, so
    seasons are separated by a gap of several months. Goals are drawn from a
    Poisson pair driven by latent team strengths; odds are derived from the same
    strengths with a fixed margin, so they carry the pre-kickoff signal a market
    model would use.

    Raises:
        ValueError: ``n_teams`` is not even and at least 2, or ``n_seasons`` is
            below 1.
    """
    if n_teams < 2 or n_teams % 2 != 0:
        raise ValueError(f"n_teams must be an even number >= 2, got {n_teams}")
    if n_seasons < 1:
        raise ValueError(f"n_seasons must be >= 1, got {n_seasons}")

    generator = np.random.default_rng(seed)
    strengths = generator.normal(0.0, 0.35, size=n_teams)
    team_ids = np.arange(1, n_teams + 1, dtype=np.int64)

    match_ids: list[int] = []
    kickoff_dates: list[datetime.date] = []
    season_ids: list[int] = []
    home_team_ids: list[int] = []
    away_team_ids: list[int] = []
    home_goals: list[int] = []
    away_goals: list[int] = []
    results: list[int] = []
    odds: list[NDArray[np.float64]] = []
    sources: list[str] = []

    for season_index in range(n_seasons):
        season_id = season_index + 1
        season_start = _first_saturday(_FIRST_SEASON_YEAR + season_index)
        for round_index, home, away in _season_schedule(team_ids):
            edge = float(strengths[home - 1] - strengths[away - 1])
            home_score, away_score = _draw_score(generator, edge)
            match_ids.append(len(match_ids) + 1)
            kickoff_dates.append(
                season_start + datetime.timedelta(days=_ROUND_STEP_DAYS * round_index)
            )
            season_ids.append(season_id)
            home_team_ids.append(home)
            away_team_ids.append(away)
            home_goals.append(home_score)
            away_goals.append(away_score)
            results.append(_result_index(home_score, away_score))
            odds.append(_ODDS_MARGIN / _outcome_probabilities(edge))
            sources.append(_BOOKMAKER)

    n = len(match_ids)
    return MatchData(
        match_id=np.array(match_ids, dtype=np.int64),
        kickoff_date=np.array(kickoff_dates, dtype="datetime64[D]"),
        season_id=np.array(season_ids, dtype=np.int64),
        home_team_id=np.array(home_team_ids, dtype=np.int64),
        away_team_id=np.array(away_team_ids, dtype=np.int64),
        odds=np.array(odds, dtype=np.float64).reshape(n, _RESULT_COUNT),
        odds_source=tuple(sources),
        home_goals=np.array(home_goals, dtype=np.int64),
        away_goals=np.array(away_goals, dtype=np.int64),
        result=np.array(results, dtype=np.int8),
    )


def make_history(
    results: Sequence[int], *, kickoff_date: datetime.date = _DEFAULT_HISTORY_DATE
) -> MatchData:
    """Return a history whose ``result`` column is exactly ``results``.

    Every row shares one kickoff date and the match ids ascend, which keeps the
    rows sorted. Goals follow a canonical scoreline per result, and no row
    carries odds.

    Raises:
        ValueError: a result is not ``0``, ``1`` or ``2``.
    """
    n = len(results)
    result = np.asarray(results, dtype=np.int8)
    if n and bool(((result < 0) | (result > _RESULT_COUNT - 1)).any()):
        raise ValueError(f"results must be 0 (H), 1 (D) or 2 (A), got {list(results)}")
    scores = np.array([_SCORE_FOR_RESULT[int(value)] for value in result], dtype=np.int64)
    return MatchData(
        match_id=np.arange(1, n + 1, dtype=np.int64),
        kickoff_date=np.full(n, np.datetime64(kickoff_date, "D"), dtype="datetime64[D]"),
        season_id=np.ones(n, dtype=np.int64),
        home_team_id=np.full(n, 1, dtype=np.int64),
        away_team_id=np.full(n, 2, dtype=np.int64),
        odds=np.full((n, _RESULT_COUNT), np.nan, dtype=np.float64),
        odds_source=tuple(None for _ in range(n)),
        home_goals=scores.reshape(n, 2)[:, 0].copy(),
        away_goals=scores.reshape(n, 2)[:, 1].copy(),
        result=result,
    )


def make_fixtures(n: int = 3, *, kickoff_date: datetime.date = _DEFAULT_FIXTURE_DATE) -> Fixtures:
    """Return ``n`` fixtures with no odds, dated well after :func:`make_history`."""
    return make_history([0] * n, kickoff_date=kickoff_date).as_fixtures(np.ones(n, dtype=np.bool_))


def to_date(value: np.datetime64 | datetime.date) -> datetime.date:
    """Convert a NumPy date to a :class:`datetime.date`."""
    if isinstance(value, datetime.date):
        return value
    converted = np.datetime64(value, "D").astype(object)
    if not isinstance(converted, datetime.date):
        raise TypeError(f"expected a date, got {value!r}")
    return converted


def history_before(data: MatchData, cutoff: datetime.date) -> History:
    """Return the played matches dated strictly before ``cutoff``."""
    return data.select(data.kickoff_date < np.datetime64(cutoff, "D"))


def fixtures_between(
    data: MatchData, start: datetime.date, *, days: int = _WINDOW_DAYS
) -> Fixtures:
    """Return the fixtures dated in ``[start, start + days)``, with no outcome column."""
    end = start + datetime.timedelta(days=days)
    mask = (data.kickoff_date >= np.datetime64(start, "D")) & (
        data.kickoff_date < np.datetime64(end, "D")
    )
    return data.as_fixtures(mask)


def fit_and_predict(
    model_name: str,
    data: MatchData,
    cutoff: datetime.date,
    *,
    days: int = _WINDOW_DAYS,
) -> NDArray[np.float64]:
    """Fit a fresh model on ``data`` before ``cutoff`` and predict the next window."""
    model = make_model(model_name, seed=DEFAULT_SEED)
    model.fit(history_before(data, cutoff), cutoff)
    return model.predict(fixtures_between(data, cutoff, days=days))


def perturb_results_from(data: MatchData, start: datetime.date) -> MatchData:
    """Return a copy with a different result for every match on or after ``start``.

    The new result is the old one shifted by one position in ``H, D, A``, and the
    goals are rewritten to a canonical scoreline for it, so no perturbed row
    keeps its old outcome. Dates, ids and odds are untouched: odds are
    pre-kickoff information, so a model may legitimately depend on them.
    """
    mask = data.kickoff_date >= np.datetime64(start, "D")
    result = np.where(mask, (data.result.astype(np.intp) + 1) % _RESULT_COUNT, data.result).astype(
        np.int8
    )
    home_goals = np.array([score[0] for score in _SCORE_FOR_RESULT], dtype=np.int64)
    away_goals = np.array([score[1] for score in _SCORE_FOR_RESULT], dtype=np.int64)
    index = result.astype(np.intp)
    return MatchData(
        match_id=data.match_id,
        kickoff_date=data.kickoff_date,
        season_id=data.season_id,
        home_team_id=data.home_team_id,
        away_team_id=data.away_team_id,
        odds=data.odds,
        odds_source=data.odds_source,
        home_goals=np.where(mask, home_goals[index], data.home_goals).astype(np.int64),
        away_goals=np.where(mask, away_goals[index], data.away_goals).astype(np.int64),
        result=result,
    )


def middle_cutoff(data: MatchData) -> datetime.date:
    """Return the median distinct kickoff date: history and a window on both sides."""
    distinct = np.unique(data.kickoff_date)
    return to_date(distinct[len(distinct) // 2])


def assert_fit_predict_invariant(model_name: str) -> None:
    """Assert one fit-and-predict call does not read results dated on or after the cutoff.

    The model is fitted at a cutoff ``c`` and predicts ``[c, c + 7)``. A second
    dataset replaces the goals and result of every match dated on or after ``c``,
    including matches earlier in the same window, and leaves the odds alone. The
    probabilities must be bit-identical.
    """
    data = make_synthetic_league()
    cutoff = middle_cutoff(data)
    in_window = data.kickoff_date >= np.datetime64(cutoff, "D")
    assert bool(in_window.any()), f"{model_name}: no match in the window at {cutoff}"
    perturbed_data = perturb_results_from(data, cutoff)
    assert not np.array_equal(perturbed_data.result[in_window], data.result[in_window]), (
        "the perturbation changed no result, so the invariance check would be vacuous"
    )
    reference = fit_and_predict(model_name, data, cutoff)
    perturbed = fit_and_predict(model_name, perturbed_data, cutoff)
    assert reference.shape[0] > 0, f"{model_name}: the comparison window is empty"
    np.testing.assert_array_equal(
        perturbed,
        reference,
        err_msg=(
            f"{model_name}: probabilities changed when the results dated on or after "
            f"the cutoff {cutoff.isoformat()} were replaced"
        ),
    )


def assert_walk_forward_invariant(model_name: str) -> None:
    """Assert every walk-forward window leaves the future results alone.

    Cutoffs run from the first kickoff date through the middle distinct kickoff
    date. A second dataset replaces the goals and result of every match dated on
    or after that boundary, including matches inside the earlier windows. The
    scored matches and the probabilities must be bit-identical.
    """
    data = make_synthetic_league()
    boundary = middle_cutoff(data)
    first = to_date(data.kickoff_date[0])
    last = to_date(data.kickoff_date[-1])
    cutoffs = [cutoff for cutoff in weekly_cutoffs(first, last) if cutoff <= boundary]
    perturbed_data = perturb_results_from(data, boundary)
    changed = data.kickoff_date >= np.datetime64(boundary, "D")
    assert not np.array_equal(perturbed_data.result[changed], data.result[changed]), (
        "the perturbation changed no result, so the invariance check would be vacuous"
    )
    reference = walk_forward(data, model_name, seed=DEFAULT_SEED, cutoffs=cutoffs)
    perturbed = walk_forward(perturbed_data, model_name, seed=DEFAULT_SEED, cutoffs=cutoffs)
    assert len(reference) > 0, f"{model_name}: the walk-forward run scored no match"
    np.testing.assert_array_equal(
        perturbed.match_id,
        reference.match_id,
        err_msg=f"{model_name}: match ids changed at the boundary {boundary.isoformat()}",
    )
    np.testing.assert_array_equal(
        perturbed.probabilities,
        reference.probabilities,
        err_msg=(
            f"{model_name}: probabilities changed when the results dated on or after "
            f"the boundary {boundary.isoformat()} were replaced"
        ),
    )


def _season_schedule(team_ids: NDArray[np.int64]) -> list[tuple[int, int, int]]:
    """Return ``(round, home, away)`` for one double round robin."""
    first_half = [
        [(away, home) if round_index % 2 else (home, away) for home, away in pairs]
        for round_index, pairs in enumerate(_round_robin(team_ids))
    ]
    schedule = [
        (round_index, home, away)
        for round_index, pairs in enumerate(first_half)
        for home, away in pairs
    ]
    offset = len(first_half)
    schedule.extend(
        (offset + round_index, away, home)
        for round_index, pairs in enumerate(first_half)
        for home, away in pairs
    )
    return schedule


def _round_robin(team_ids: NDArray[np.int64]) -> list[list[tuple[int, int]]]:
    """Return the first-half rounds of a round robin: every pair meets once."""
    order = [int(team_id) for team_id in team_ids]
    n = len(order)
    rounds = []
    for _ in range(n - 1):
        rounds.append([(order[i], order[n - 1 - i]) for i in range(n // 2)])
        order = [order[0], order[-1], *order[1:-1]]
    return rounds


def _first_saturday(year: int) -> datetime.date:
    """Return the first Saturday on or after the season start day."""
    day = datetime.date(year, _SEASON_START_MONTH, _SEASON_START_DAY)
    return day + datetime.timedelta(days=(5 - day.weekday()) % 7)


def _outcome_probabilities(edge: float) -> NDArray[np.float64]:
    """Return synthetic H/D/A probabilities, increasing in the home strength edge."""
    home = 1.0 / (1.0 + float(np.exp(-edge)))
    draw = float(np.clip(0.27 - 0.12 * abs(edge), 0.18, 0.32))
    rest = 1.0 - draw
    return np.array([rest * home, draw, rest * (1.0 - home)], dtype=np.float64)


def _draw_score(generator: np.random.Generator, edge: float) -> tuple[int, int]:
    """Return a synthetic scoreline drawn from two Poisson rates."""
    home_rate = float(np.exp(0.15 + 0.5 * edge))
    away_rate = float(np.exp(0.15 - 0.5 * edge))
    return (
        min(int(generator.poisson(home_rate)), _GOALS_CAP),
        min(int(generator.poisson(away_rate)), _GOALS_CAP),
    )


def _result_index(home_goals: int, away_goals: int) -> int:
    if home_goals > away_goals:
        return 0
    return 1 if home_goals == away_goals else 2
