"""Tests for the scored metrics.

Every expected value is hand computed: a uniform forecast over one row per
outcome, a certain forecast, its opposite, and one skewed row whose cumulative
error is written out in a comment. All data is synthetic.
"""

from collections.abc import Callable

import numpy as np
import pytest
from lmi_core.evaluation.metrics import (
    accuracy,
    brier,
    calibration_bins,
    log_loss,
    one_hot,
    rps,
)
from numpy.typing import NDArray

Metric = Callable[[NDArray[np.float64], NDArray[np.int8]], float]
METRICS: tuple[Metric, ...] = (log_loss, brier, rps, accuracy)

HOME, DRAW, AWAY = 0, 1, 2
OUTCOMES = ("H", "D", "A")
UNIFORM = 1 / 3
LN_3 = 1.0986122886681098
LOG_OF_THE_CLIP = 34.538776394910685  # -ln(1e-15), the value log_loss clips to.

# Three scored rows plus a trailing row that carries no forecast. The home
# forecasts sit in bin 5, the away forecasts in bin 1.
SPREAD = np.array(
    [
        [0.55, 0.30, 0.15],
        [0.52, 0.33, 0.15],
        [0.58, 0.27, 0.15],
        [np.nan, np.nan, np.nan],
    ]
)
SPREAD_RESULTS = np.array([HOME, DRAW, HOME, AWAY], dtype=np.int8)


def test_uniform_forecast_scores_the_three_outcomes() -> None:
    uniform = np.full((3, 3), UNIFORM)
    results = np.array([HOME, DRAW, AWAY], dtype=np.int8)

    assert log_loss(uniform, results) == pytest.approx(LN_3)
    assert brier(uniform, results) == pytest.approx(2 / 3)
    assert rps(uniform, results) == pytest.approx(2 / 9)
    assert accuracy(uniform, results) == pytest.approx(UNIFORM)


def test_uniform_home_forecast_has_ranked_probability_score_of_five_eighteenths() -> None:
    # P = (1/3, 2/3) against Y = (1, 1): (4/9 + 1/9) / 2 = 5/18.
    probabilities = np.array([[UNIFORM, UNIFORM, UNIFORM]])

    assert rps(probabilities, np.array([HOME], dtype=np.int8)) == pytest.approx(5 / 18)


def test_perfect_forecast_scores_zero() -> None:
    probabilities = np.array([[1.0, 0.0, 0.0]])
    results = np.array([HOME], dtype=np.int8)

    assert log_loss(probabilities, results) == 0.0
    assert brier(probabilities, results) == 0.0
    assert rps(probabilities, results) == 0.0
    assert accuracy(probabilities, results) == 1.0


def test_opposite_forecast_scores_the_worst_values() -> None:
    probabilities = np.array([[0.0, 0.0, 1.0]])
    results = np.array([HOME], dtype=np.int8)

    assert log_loss(probabilities, results) == pytest.approx(LOG_OF_THE_CLIP)
    assert brier(probabilities, results) == 2.0
    assert rps(probabilities, results) == 1.0
    assert accuracy(probabilities, results) == 0.0


def test_skewed_forecast_matches_hand_computed_values() -> None:
    # RPS: P = (0.5, 0.8) against Y = (0, 1), so (0.25 + 0.04) / 2.
    # Brier: 0.25 + 0.49 + 0.04.
    probabilities = np.array([[0.5, 0.3, 0.2]])

    assert rps(probabilities, np.array([DRAW], dtype=np.int8)) == pytest.approx(0.145)
    assert brier(probabilities, np.array([DRAW], dtype=np.int8)) == pytest.approx(0.78)


def test_one_hot_encodes_the_result_in_outcome_order() -> None:
    expected = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])

    encoded = one_hot(np.array([HOME, DRAW, AWAY], dtype=np.int8))

    np.testing.assert_array_equal(encoded, expected)


@pytest.mark.parametrize("metric", METRICS)
def test_all_nan_rows_do_not_shift_the_scores(metric: Metric) -> None:
    forecast = np.array([[0.5, 0.3, 0.2]])
    results = np.array([DRAW], dtype=np.int8)
    blank = np.full((1, 3), np.nan)
    padded = np.vstack([blank, forecast, blank])
    padded_results = np.array([HOME, DRAW, HOME], dtype=np.int8)

    assert metric(padded, padded_results) == metric(forecast, results)


@pytest.mark.parametrize("metric", METRICS)
def test_a_forecast_without_a_scored_row_raises(metric: Metric) -> None:
    every_row_blank = np.full((2, 3), np.nan)
    outcomes = np.array([HOME, AWAY], dtype=np.int8)

    with pytest.raises(ValueError, match="no forecast to score"):
        metric(every_row_blank, outcomes)
    with pytest.raises(ValueError, match="no forecast to score"):
        metric(np.zeros((0, 3)), np.zeros(0, dtype=np.int8))


@pytest.mark.parametrize("metric", METRICS)
def test_wrong_shapes_raise(metric: Metric) -> None:
    with pytest.raises(ValueError):
        metric(np.array([0.5, 0.3, 0.2]), np.array([HOME], dtype=np.int8))
    with pytest.raises(ValueError):
        metric(np.full((2, 2), 0.5), np.array([HOME, DRAW], dtype=np.int8))
    with pytest.raises(ValueError):
        metric(np.full((2, 3), UNIFORM), np.array([HOME], dtype=np.int8))


@pytest.mark.parametrize("metric", METRICS)
def test_a_row_that_mixes_nan_with_values_raises(metric: Metric) -> None:
    probabilities = np.array([[0.5, np.nan, 0.5], [0.5, 0.3, 0.2]])
    results = np.array([HOME, DRAW], dtype=np.int8)

    with pytest.raises(ValueError, match="mixes NaN"):
        metric(probabilities, results)


def test_calibration_bins_cover_every_scored_row_for_each_outcome() -> None:
    rows = calibration_bins(SPREAD, SPREAD_RESULTS, model="spread")

    assert len(rows) == 3 * 10
    assert [(row.outcome, row.bin_index) for row in rows] == [
        (outcome, index) for outcome in OUTCOMES for index in range(10)
    ]
    assert {row.model for row in rows} == {"spread"}
    for outcome in OUTCOMES:
        assert sum(row.n for row in rows if row.outcome == outcome) == 3


def test_a_filled_bin_reports_the_mean_forecast_and_observed_frequency() -> None:
    rows = calibration_bins(SPREAD, SPREAD_RESULTS, model="spread")

    # 0.55, 0.52 and 0.58 all fall in the home bin [0.5, 0.6); two of them ended H.
    home = next(row for row in rows if row.outcome == "H" and row.bin_index == 5)
    assert home.bin_lower == pytest.approx(0.5)
    assert home.bin_upper == pytest.approx(0.6)
    assert home.n == 3
    assert home.mean_predicted == pytest.approx((0.55 + 0.52 + 0.58) / 3)
    assert home.observed_frequency == pytest.approx(2 / 3)

    # 0.15 is the away forecast of all three rows and none of them ended A.
    away = next(row for row in rows if row.outcome == "A" and row.bin_index == 1)
    assert away.n == 3
    assert away.mean_predicted == pytest.approx(0.15)
    assert away.observed_frequency == 0.0


def test_a_constant_forecast_fills_exactly_one_bin_per_outcome() -> None:
    probabilities = np.tile(np.array([0.5, 0.3, 0.2]), (4, 1))
    results = np.array([HOME, DRAW, AWAY, HOME], dtype=np.int8)

    rows = calibration_bins(probabilities, results, model="constant")

    for outcome, expected_bin in zip(OUTCOMES, (5, 3, 2), strict=True):
        filled = [row for row in rows if row.outcome == outcome and row.n > 0]
        assert [row.bin_index for row in filled] == [expected_bin]
        assert filled[0].n == 4
    for row in rows:
        if row.n == 0:
            assert row.mean_predicted is None
            assert row.observed_frequency is None


def test_a_certain_forecast_lands_in_the_last_bin() -> None:
    probabilities = np.array([[1.0, 0.0, 0.0]])
    results = np.array([HOME], dtype=np.int8)

    rows = calibration_bins(probabilities, results, model="certain")

    home = rows[9]
    assert (home.outcome, home.bin_index) == ("H", 9)
    assert home.bin_lower == pytest.approx(0.9)
    assert home.bin_upper == 1.0
    assert home.n == 1
    assert home.mean_predicted == 1.0
    assert home.observed_frequency == 1.0

    away = rows[20]
    assert (away.outcome, away.bin_index) == ("A", 0)
    assert away.n == 1
    assert away.mean_predicted == 0.0
    assert away.observed_frequency == 0.0


def test_calibration_bins_reject_a_bad_input() -> None:
    blank = np.full((2, 3), np.nan)
    with pytest.raises(ValueError, match="no forecast to score"):
        calibration_bins(blank, np.array([HOME, AWAY], dtype=np.int8), model="blank")
    narrow = np.full((2, 2), 0.5)
    with pytest.raises(ValueError):
        calibration_bins(narrow, np.array([HOME, DRAW], dtype=np.int8), model="narrow")
    with pytest.raises(ValueError, match="mixes NaN"):
        calibration_bins(
            np.array([[0.5, np.nan, 0.5]]), np.array([HOME], dtype=np.int8), model="mixed"
        )
    with pytest.raises(ValueError, match="n_bins"):
        calibration_bins(
            np.full((1, 3), UNIFORM), np.array([HOME], dtype=np.int8), model="uniform", n_bins=0
        )
