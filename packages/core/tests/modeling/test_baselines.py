"""Tests for the baselines and for the model registry.

``home_win`` and ``base_rate`` are the only models this task builds, so the
registry is tested for the two resolvable names and for the loud failure of the
three names whose modules a later task adds.
"""

import datetime
import importlib

import numpy as np
import pytest
from lmi_core.modeling.base import Model
from lmi_core.modeling.baselines import BaseRateModel, HomeWinModel
from lmi_core.modeling.registry import MODEL_KINDS, MODEL_NAMES, make_model
from lmi_core.modeling.types import History

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
UNBUILT_MODELS = ("market", "elo", "dixon_coles")


def test_model_names_are_the_five_public_names() -> None:
    assert MODEL_NAMES == ("home_win", "base_rate", "market", "elo", "dixon_coles")


def test_model_kinds_classify_every_name() -> None:
    assert dict(MODEL_KINDS) == {
        "home_win": "baseline",
        "base_rate": "baseline",
        "market": "market",
        "elo": "model",
        "dixon_coles": "model",
    }


@pytest.mark.parametrize(
    ("name", "expected"),
    [("home_win", HomeWinModel), ("base_rate", BaseRateModel)],
)
def test_make_model_resolves_the_baselines(name: str, expected: type[Model]) -> None:
    model = make_model(name, seed=SEED)

    assert isinstance(model, expected)
    assert model.seed == SEED
    assert model.name == name


def test_make_model_rejects_an_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown model"):
        make_model("poisson", seed=SEED)


@pytest.mark.parametrize("name", UNBUILT_MODELS)
def test_make_model_raises_while_the_model_module_does_not_exist(name: str) -> None:
    try:
        importlib.import_module(f"lmi_core.modeling.{name}")
    except ModuleNotFoundError:
        with pytest.raises(ModuleNotFoundError):
            make_model(name, seed=SEED)
    else:
        pytest.importorskip(
            f"lmi_core.modeling.{name}",
            reason=f"lmi_core.modeling.{name} exists, so this test no longer applies",
        )


def test_home_win_predicts_the_home_side_with_certainty() -> None:
    data = make_synthetic_league(n_teams=4, n_seasons=1)
    cutoff = middle_cutoff(data)
    model = make_model("home_win", seed=SEED)
    model.fit(history_before(data, cutoff), cutoff)

    probabilities = model.predict(fixtures_between(data, cutoff))
    rows = probabilities.shape[0]

    assert rows > 0
    assert np.array_equal(probabilities[:, 0], np.ones(rows))
    assert np.array_equal(probabilities[:, 1:], np.zeros((rows, 2)))


def test_home_win_has_no_hyperparameters() -> None:
    model = HomeWinModel(seed=SEED)

    assert model.name == "home_win"
    assert model.version == "1"
    assert model.describe() == {"name": "home_win", "version": "1", "hyperparameters": {}}


def test_base_rate_predicts_the_frequencies_of_its_history() -> None:
    history = make_history([0, 0, 0, 0, 0, 1, 1, 1, 2, 2])
    model = BaseRateModel(seed=SEED)
    model.fit(history, CUTOFF)

    probabilities = model.predict(make_fixtures(3))

    np.testing.assert_allclose(
        probabilities, np.tile(np.array([0.5, 0.3, 0.2]), (3, 1)), rtol=0.0, atol=1e-12
    )


def test_base_rate_is_uniform_without_history() -> None:
    empty = make_history([])
    assert isinstance(empty, History)
    assert len(empty) == 0
    model = BaseRateModel(seed=SEED)
    model.fit(empty, CUTOFF)

    probabilities = model.predict(make_fixtures(3))

    uniform = np.tile(np.array([1 / 3, 1 / 3, 1 / 3]), (3, 1))
    np.testing.assert_allclose(probabilities, uniform, rtol=0.0, atol=1e-12)


def test_base_rate_reports_no_tuned_values() -> None:
    model = BaseRateModel(seed=SEED)

    assert model.name == "base_rate"
    assert model.version == "1"
    assert model.describe() == {"name": "base_rate", "version": "1", "hyperparameters": {}}


def test_base_rate_predicts_one_constant_row_per_fixture() -> None:
    data = make_synthetic_league(n_teams=4, n_seasons=1)
    cutoff = middle_cutoff(data)
    model = BaseRateModel(seed=SEED)
    model.fit(history_before(data, cutoff), cutoff)

    fixtures = fixtures_between(data, cutoff)
    probabilities = model.predict(fixtures)

    assert probabilities.shape == (len(fixtures), 3)
    assert np.array_equal(probabilities, np.tile(probabilities[0], (len(fixtures), 1)))


@pytest.mark.parametrize("name", ["home_win", "base_rate"])
def test_fit_predict_invariance(name: str) -> None:
    assert_fit_predict_invariant(name)


@pytest.mark.parametrize("name", ["home_win", "base_rate"])
def test_walk_forward_invariance(name: str) -> None:
    assert_walk_forward_invariant(name)
