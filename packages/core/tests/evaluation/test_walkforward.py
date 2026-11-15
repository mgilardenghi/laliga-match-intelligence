"""Tests for the walk-forward windows.

The expectations are hand computed from the synthetic league: the calendar
gives the window membership, and the hand-rolled loop calls the same model
interface the runner calls, one cutoff at a time.
"""

import datetime

import numpy as np
import pytest
from lmi_core.evaluation.walkforward import WindowPredictions, walk_forward, weekly_cutoffs
from lmi_core.modeling.registry import make_model
from lmi_core.modeling.types import MatchData
from modeling.leakage_harness import (
    DEFAULT_SEED,
    fixtures_between,
    history_before,
    make_synthetic_league,
    to_date,
)
from numpy.typing import NDArray

MODELS = ("home_win", "base_rate")
CUTOFFS = (datetime.date(2005, 9, 5), datetime.date(2005, 10, 3))


def test_weekly_cutoffs_start_on_the_monday_on_or_before_first() -> None:
    cutoffs = weekly_cutoffs(datetime.date(2005, 8, 6), datetime.date(2005, 8, 31))

    assert cutoffs == [
        datetime.date(2005, 8, 1),
        datetime.date(2005, 8, 8),
        datetime.date(2005, 8, 15),
        datetime.date(2005, 8, 22),
        datetime.date(2005, 8, 29),
    ]
    assert all(cutoff.weekday() == 0 for cutoff in cutoffs)
    assert cutoffs == sorted(cutoffs)
    assert cutoffs[-1] <= datetime.date(2005, 8, 31)
    assert cutoffs[-1] + datetime.timedelta(days=7) > datetime.date(2005, 8, 31)


def test_weekly_cutoffs_keep_a_monday_first() -> None:
    monday = datetime.date(2005, 8, 1)

    cutoffs = weekly_cutoffs(monday, datetime.date(2005, 8, 15))

    assert cutoffs == [monday, datetime.date(2005, 8, 8), datetime.date(2005, 8, 15)]


def test_weekly_cutoffs_are_empty_when_first_is_after_last() -> None:
    assert weekly_cutoffs(datetime.date(2005, 8, 8), datetime.date(2005, 8, 1)) == []


@pytest.mark.parametrize("name", MODELS)
def test_walk_forward_matches_a_hand_rolled_loop(name: str) -> None:
    data = make_synthetic_league()

    expected_match_ids: list[NDArray[np.int64]] = []
    expected_probabilities: list[NDArray[np.float64]] = []
    expected_results: list[NDArray[np.int8]] = []
    expected_seasons: list[NDArray[np.int64]] = []
    for cutoff in CUTOFFS:
        mask = _window_mask(data, cutoff)
        model = make_model(name, seed=DEFAULT_SEED)
        model.fit(history_before(data, cutoff), cutoff)
        expected_match_ids.append(data.match_id[mask])
        expected_probabilities.append(model.predict(fixtures_between(data, cutoff)))
        expected_results.append(data.result[mask])
        expected_seasons.append(data.season_id[mask])

    predictions = walk_forward(data, name, seed=DEFAULT_SEED, cutoffs=CUTOFFS)

    np.testing.assert_array_equal(predictions.match_id, np.concatenate(expected_match_ids))
    np.testing.assert_array_equal(
        predictions.probabilities, np.concatenate(expected_probabilities, axis=0)
    )
    np.testing.assert_array_equal(predictions.result, np.concatenate(expected_results))
    np.testing.assert_array_equal(predictions.season_id, np.concatenate(expected_seasons))


def test_the_full_cutoff_range_scores_every_played_match_exactly_once() -> None:
    data = make_synthetic_league()
    cutoffs = weekly_cutoffs(to_date(data.kickoff_date[0]), to_date(data.kickoff_date[-1]))

    predictions = walk_forward(data, "base_rate", seed=DEFAULT_SEED, cutoffs=cutoffs)

    fixtures_per_window = [int(np.count_nonzero(_window_mask(data, cutoff))) for cutoff in cutoffs]
    assert len(predictions) == sum(fixtures_per_window)
    assert len(predictions) == len(data)
    assert np.unique(predictions.match_id).size == predictions.match_id.size
    np.testing.assert_array_equal(np.sort(predictions.match_id), np.sort(data.match_id))


def test_a_window_without_a_fixture_contributes_nothing() -> None:
    data = make_synthetic_league()
    summer_gap = datetime.date(2006, 6, 5)
    with_fixtures = datetime.date(2006, 9, 4)

    with_gap = walk_forward(
        data, "base_rate", seed=DEFAULT_SEED, cutoffs=[summer_gap, with_fixtures]
    )
    without_gap = walk_forward(data, "base_rate", seed=DEFAULT_SEED, cutoffs=[with_fixtures])

    np.testing.assert_array_equal(with_gap.match_id, without_gap.match_id)
    np.testing.assert_array_equal(with_gap.probabilities, without_gap.probabilities)
    np.testing.assert_array_equal(with_gap.result, without_gap.result)
    np.testing.assert_array_equal(with_gap.season_id, without_gap.season_id)


def test_step_days_moves_the_window_edge() -> None:
    data = make_synthetic_league()
    cutoff = datetime.date(2005, 9, 5)

    one_week = walk_forward(data, "base_rate", seed=DEFAULT_SEED, cutoffs=[cutoff])
    two_weeks = walk_forward(data, "base_rate", seed=DEFAULT_SEED, cutoffs=[cutoff], step_days=14)

    assert len(two_weeks) > len(one_week)
    np.testing.assert_array_equal(
        two_weeks.match_id, data.match_id[_window_mask(data, cutoff, step_days=14)]
    )
    np.testing.assert_array_equal(
        one_week.match_id, data.match_id[_window_mask(data, cutoff, step_days=7)]
    )


def test_no_cutoffs_and_no_fixture_cutoffs_return_empty_arrays() -> None:
    data = make_synthetic_league()

    no_cutoffs = walk_forward(data, "base_rate", seed=DEFAULT_SEED, cutoffs=[])
    summer = walk_forward(data, "base_rate", seed=DEFAULT_SEED, cutoffs=[datetime.date(2006, 6, 5)])

    for predictions in (no_cutoffs, summer):
        assert isinstance(predictions, WindowPredictions)
        assert len(predictions) == 0
        assert predictions.probabilities.shape == (0, 3)
        assert predictions.match_id.dtype == np.dtype(np.int64)
        assert predictions.result.dtype == np.dtype(np.int8)
        assert predictions.season_id.dtype == np.dtype(np.int64)


def test_walk_forward_rejects_an_invalid_step_and_bad_cutoff_order() -> None:
    data = make_synthetic_league()
    cutoff = datetime.date(2005, 9, 5)

    with pytest.raises(ValueError, match="step_days"):
        walk_forward(data, "base_rate", seed=DEFAULT_SEED, cutoffs=[cutoff], step_days=0)
    with pytest.raises(ValueError, match="strictly ascending"):
        walk_forward(data, "base_rate", seed=DEFAULT_SEED, cutoffs=[cutoff, cutoff])
    with pytest.raises(ValueError, match="strictly ascending"):
        walk_forward(
            data,
            "base_rate",
            seed=DEFAULT_SEED,
            cutoffs=[cutoff + datetime.timedelta(days=7), cutoff],
        )


def _window_mask(
    data: MatchData, cutoff: datetime.date, *, step_days: int = 7
) -> NDArray[np.bool_]:
    """Return the fixtures a window would predict."""
    start = np.datetime64(cutoff, "D")
    end = start + np.timedelta64(step_days, "D")
    return (data.kickoff_date >= start) & (data.kickoff_date < end)
