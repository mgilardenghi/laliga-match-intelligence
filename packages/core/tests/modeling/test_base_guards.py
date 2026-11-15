"""Tests for the leakage guards in :mod:`lmi_core.modeling.base`.

The guards are the only thing standing between a model and a future result, so
they are tested through the public ``fit``/``predict`` API with spy models that
record what they were handed.
"""

import datetime
from collections.abc import Callable

import numpy as np
import pytest
from lmi_core.modeling.base import Model
from lmi_core.modeling.types import Fixtures, History, LeakageError
from numpy.typing import NDArray

from modeling.leakage_harness import (
    fixtures_between,
    history_before,
    make_fixtures,
    make_synthetic_league,
    middle_cutoff,
    to_date,
)

WINDOW_DAYS = 7


class SpyModel(Model):
    """A model that records the data of every call it receives."""

    name = "spy"
    version = "0"

    def __init__(self, *, seed: int = 0) -> None:
        super().__init__(seed=seed)
        self.fit_calls: list[tuple[History, datetime.date]] = []
        self.predict_calls: list[Fixtures] = []

    def _fit(self, history: History, cutoff: datetime.date) -> None:
        self.fit_calls.append((history, cutoff))

    def _predict(self, fixtures: Fixtures) -> NDArray[np.float64]:
        self.predict_calls.append(fixtures)
        return np.full((len(fixtures), 3), 1.0 / 3.0)


class FixedOutputModel(Model):
    """A model whose probabilities are supplied by the test."""

    name = "fixed_output"
    version = "0"

    def __init__(self, output: Callable[[int], NDArray[np.float64]]) -> None:
        super().__init__(seed=0)
        self._output = output

    def _fit(self, history: History, cutoff: datetime.date) -> None:
        """Nothing to fit."""

    def _predict(self, fixtures: Fixtures) -> NDArray[np.float64]:
        return self._output(len(fixtures))


def _league() -> History:
    """Return a small league: four teams, one double round robin."""
    return make_synthetic_league(n_teams=4, n_seasons=1)


def _fitted_spy(*, seed: int = 3) -> tuple[SpyModel, datetime.date]:
    """Return a fitted spy model and the cutoff it was fitted at."""
    cutoff = middle_cutoff(_league())
    model = SpyModel(seed=seed)
    model.fit(history_before(_league(), cutoff), cutoff)
    return model, cutoff


def _fitted_fixed(output: Callable[[int], NDArray[np.float64]]) -> FixedOutputModel:
    """Return a fitted model that returns ``output(n)`` for ``n`` fixtures."""
    cutoff = middle_cutoff(_league())
    model = FixedOutputModel(output)
    model.fit(history_before(_league(), cutoff), cutoff)
    return model


def test_fit_rejects_a_history_dated_on_or_after_the_cutoff() -> None:
    data = _league()
    cutoff = middle_cutoff(data)
    cutoff_day = np.datetime64(cutoff, "D")
    inclusive = data.select(data.kickoff_date <= cutoff_day)
    later = data.select(data.kickoff_date > cutoff_day)
    assert to_date(inclusive.kickoff_date.max()) == cutoff

    model = SpyModel()

    with pytest.raises(LeakageError, match="on or after"):
        model.fit(inclusive, cutoff)
    with pytest.raises(LeakageError, match="on or after"):
        model.fit(later, cutoff)


def test_fit_accepts_history_strictly_before_the_cutoff() -> None:
    model, cutoff = _fitted_spy()

    recorded_history, recorded_cutoff = model.fit_calls[0]

    assert recorded_cutoff == cutoff
    assert to_date(recorded_history.kickoff_date.max()) < cutoff


def test_predict_rejects_a_fixture_dated_before_the_cutoff() -> None:
    data = _league()
    model, cutoff = _fitted_spy()
    earlier = data.as_fixtures(data.kickoff_date < np.datetime64(cutoff, "D"))

    with pytest.raises(LeakageError, match="before the cutoff"):
        model.predict(earlier)


def test_predict_rejects_a_history() -> None:
    model, cutoff = _fitted_spy()
    leaking = history_before(_league(), cutoff)

    with pytest.raises(LeakageError, match="History"):
        model.predict(leaking)


def test_predict_before_fit_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="before fit"):
        SpyModel().predict(make_fixtures(2))


def _wrong_width(rows: int) -> NDArray[np.float64]:
    return np.zeros((rows, 2), dtype=np.float64)


def _negative(rows: int) -> NDArray[np.float64]:
    return np.tile(np.array([1.5, -0.5, 0.0]), (rows, 1))


def _sums_to_more_than_one(rows: int) -> NDArray[np.float64]:
    return np.tile(np.array([0.5, 0.5, 0.5]), (rows, 1))


def _mixed_nan(rows: int) -> NDArray[np.float64]:
    return np.tile(np.array([np.nan, 0.5, 0.5]), (rows, 1))


def _all_nan(rows: int) -> NDArray[np.float64]:
    return np.full((rows, 3), np.nan, dtype=np.float64)


@pytest.mark.parametrize(
    ("output", "message"),
    [
        (_wrong_width, r"shape \(2, 3\)"),
        (_negative, "negative"),
        (_sums_to_more_than_one, "sums to"),
        (_mixed_nan, "mixes NaN"),
    ],
)
def test_predict_rejects_malformed_probabilities(
    output: Callable[[int], NDArray[np.float64]], message: str
) -> None:
    model = _fitted_fixed(output)

    with pytest.raises(ValueError, match=message):
        model.predict(make_fixtures(2))


def test_predict_accepts_an_all_nan_row_as_no_forecast() -> None:
    probabilities = _fitted_fixed(_all_nan).predict(make_fixtures(2))

    assert probabilities.shape == (2, 3)
    assert bool(np.isnan(probabilities).all())


def test_driver_hands_the_model_one_clean_window() -> None:
    data = make_synthetic_league()
    cutoff = middle_cutoff(data)
    model = SpyModel()
    model.fit(history_before(data, cutoff), cutoff)

    fixtures = fixtures_between(data, cutoff, days=WINDOW_DAYS)
    model.predict(fixtures)

    recorded_history, recorded_cutoff = model.fit_calls[0]
    predicted = model.predict_calls[0]
    history_max = to_date(recorded_history.kickoff_date.max())
    fixture_min = to_date(predicted.kickoff_date.min())
    fixture_max = to_date(predicted.kickoff_date.max())
    window_end = cutoff + datetime.timedelta(days=WINDOW_DAYS)

    assert recorded_cutoff == cutoff
    assert history_max < cutoff
    assert cutoff <= fixture_min <= fixture_max < window_end
    assert len(recorded_history) > 0
    assert len(predicted) > 0
    assert not hasattr(fixtures, "result")
    assert not hasattr(predicted, "result")


def test_a_match_on_the_cutoff_belongs_to_the_fixtures() -> None:
    data = make_synthetic_league()
    cutoff = middle_cutoff(data)
    cutoff_day = np.datetime64(cutoff, "D")
    on_cutoff = np.flatnonzero(data.kickoff_date == cutoff_day)
    assert on_cutoff.size > 0

    history = history_before(data, cutoff)
    fixtures = fixtures_between(data, cutoff, days=WINDOW_DAYS)
    model = SpyModel()
    model.fit(history, cutoff)
    probabilities = model.predict(fixtures)

    assert not bool((history.kickoff_date == cutoff_day).any())
    assert np.array_equal(fixtures.match_id, data.match_id[on_cutoff])
    assert probabilities.shape == (on_cutoff.size, 3)


def test_describe_reports_identity_and_hyperparameters() -> None:
    model = SpyModel(seed=5)

    assert model.seed == 5
    assert model.describe() == {"name": "spy", "version": "0", "hyperparameters": {}}


def test_the_base_class_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        Model(seed=0)  # type: ignore[abstract]
