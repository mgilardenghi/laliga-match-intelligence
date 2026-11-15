"""Tests for the market model: margin removal and the no-odds forecast.

The synthetic league carries odds derived from the same latent strengths as the
goals, so a fixture of the league is a row the model can price. Every odds value
in these tests is synthetic; no bookmaker data appears here.
"""

import dataclasses
import datetime

import numpy as np
import pytest
from lmi_core.modeling.market import MarketModel, remove_margin
from lmi_core.modeling.registry import make_model

from modeling.leakage_harness import (
    assert_fit_predict_invariant,
    assert_walk_forward_invariant,
    fixtures_between,
    history_before,
    make_fixtures,
    make_history,
    make_synthetic_league,
    middle_cutoff,
)

SEED = 11
CUTOFF = datetime.date(2021, 1, 9)
ODDS_SHAPE = (0, 3)


def test_remove_margin_returns_the_implied_probabilities() -> None:
    probabilities = remove_margin(np.array([[2.0, 3.5, 4.0]], dtype=np.float64))

    np.testing.assert_allclose(
        probabilities, np.array([[0.482759, 0.275862, 0.241379]]), rtol=0.0, atol=1e-6
    )
    np.testing.assert_allclose(probabilities.sum(axis=1), np.ones(1), rtol=0.0, atol=1e-12)


def test_remove_margin_keeps_a_row_without_odds_missing() -> None:
    odds = np.array([[2.0, 3.5, 4.0], [np.nan, np.nan, np.nan], [3.0, 3.0, 3.0]], dtype=np.float64)

    probabilities = remove_margin(odds)

    assert np.isnan(probabilities[1]).all()
    assert np.isfinite(probabilities[[0, 2]]).all()
    np.testing.assert_allclose(probabilities[[0, 2]].sum(axis=1), np.ones(2), atol=1e-12)


def test_remove_margin_normalizes_every_row_separately() -> None:
    odds = np.array([[1.5, 4.0, 7.0], [3.0, 3.2, 2.4]], dtype=np.float64)

    probabilities = remove_margin(odds)

    inverse = 1.0 / odds
    np.testing.assert_allclose(
        probabilities, inverse / inverse.sum(axis=1, keepdims=True), rtol=0.0, atol=1e-15
    )
    assert probabilities[0, 0] > probabilities[1, 0]


@pytest.mark.parametrize(
    "odds",
    [
        np.array([[0.0, 3.5, 4.0]], dtype=np.float64),
        np.array([[2.0, -3.5, 4.0]], dtype=np.float64),
        np.array([[2.0, np.inf, 4.0]], dtype=np.float64),
    ],
)
def test_remove_margin_rejects_a_price_that_is_not_positive_and_finite(
    odds: np.ndarray[tuple[int, int], np.dtype[np.float64]],
) -> None:
    with pytest.raises(ValueError, match="positive finite decimal price"):
        remove_margin(odds)


@pytest.mark.parametrize(
    "odds",
    [np.zeros((2, 2), dtype=np.float64), np.array([2.0, 3.5], dtype=np.float64)],
)
def test_remove_margin_rejects_a_shape_that_is_not_three_outcomes(
    odds: np.ndarray[tuple[int, ...], np.dtype[np.float64]],
) -> None:
    with pytest.raises(ValueError, match="odds must have shape"):
        remove_margin(odds)


def test_remove_margin_returns_no_rows_for_no_fixtures() -> None:
    probabilities = remove_margin(np.empty(ODDS_SHAPE, dtype=np.float64))

    assert probabilities.shape == ODDS_SHAPE


def test_market_predicts_the_margin_removed_odds_of_each_fixture() -> None:
    data = make_synthetic_league(n_teams=4, n_seasons=1)
    cutoff = middle_cutoff(data)
    model = make_model("market", seed=SEED)
    model.fit(history_before(data, cutoff), cutoff)

    fixtures = fixtures_between(data, cutoff)
    probabilities = model.predict(fixtures)

    assert len(fixtures) > 0
    np.testing.assert_array_equal(probabilities, remove_margin(fixtures.odds))
    np.testing.assert_allclose(probabilities.sum(axis=1), np.ones(len(fixtures)), atol=1e-9)


def test_market_ignores_the_history() -> None:
    data = make_synthetic_league(n_teams=4, n_seasons=1)
    cutoff = middle_cutoff(data)
    fixtures = fixtures_between(data, cutoff)

    fitted_on_league = make_model("market", seed=SEED)
    fitted_on_league.fit(history_before(data, cutoff), cutoff)
    fitted_on_nothing = make_model("market", seed=SEED)
    fitted_on_nothing.fit(make_history([]), cutoff)

    np.testing.assert_array_equal(
        fitted_on_nothing.predict(fixtures), fitted_on_league.predict(fixtures)
    )


def test_market_reports_no_forecast_without_odds() -> None:
    fixtures = make_fixtures(3)
    model = make_model("market", seed=SEED)
    model.fit(make_history([]), CUTOFF)

    probabilities = model.predict(fixtures)

    assert probabilities.shape == (len(fixtures), 3)
    assert np.isnan(probabilities).all()


def test_market_reports_no_forecast_only_for_the_rows_without_odds() -> None:
    data = make_synthetic_league(n_teams=6, n_seasons=1)
    cutoff = middle_cutoff(data)
    fixtures = fixtures_between(data, cutoff)
    assert len(fixtures) > 2
    odds = np.array(fixtures.odds, dtype=np.float64)
    odds[1] = np.nan
    sources = fixtures.odds_source
    mixed = dataclasses.replace(fixtures, odds=odds, odds_source=(sources[0], None, *sources[2:]))
    model = make_model("market", seed=SEED)
    model.fit(history_before(data, cutoff), cutoff)

    probabilities = model.predict(mixed)

    assert np.isnan(probabilities[1]).all()
    others = np.arange(len(fixtures)) != 1
    assert np.isfinite(probabilities[others]).all()
    np.testing.assert_allclose(
        probabilities[others].sum(axis=1), np.ones(int(others.sum())), atol=1e-12
    )


def test_market_reports_its_identity() -> None:
    model = MarketModel(seed=SEED)

    assert model.name == "market"
    assert model.version == "1"
    assert model.describe() == {"name": "market", "version": "1", "hyperparameters": {"seed": SEED}}


def test_fit_predict_invariance() -> None:
    assert_fit_predict_invariant("market")


def test_walk_forward_invariance() -> None:
    assert_walk_forward_invariant("market")
