"""Tests for the ``DixonColesModel.score_matrix`` method.

The method returns the (n, 11, 11) score matrix of each fixture and feeds
``predict`` from the same matrix, so these tests pin the three things that only
the method owns: the aggregation into H/D/A rows agrees with ``predict``, every
matrix is a distribution, and the input guards of ``predict`` apply unchanged.
The formula of the matrix itself belongs to the module-level ``score_matrix``
function and is tested in ``tests/modeling/test_dixon_coles.py``; it is not
repeated here.
"""

import datetime

import numpy as np
import pytest
from lmi_core.modeling.dixon_coles import DixonColesModel
from lmi_core.modeling.types import History, LeakageError
from modeling.leakage_harness import (
    DEFAULT_SEED,
    history_before,
    make_fixtures,
    make_synthetic_league,
    to_date,
)

_GRID = 11
"""Goal counts per side of one score matrix: ``MAX_GOALS + 1``."""

_TOLERANCE = 1e-12


def _fitted() -> tuple[DixonColesModel, datetime.date, History]:
    """Return a model fitted on a synthetic league, its cutoff, and the whole league."""
    data = make_synthetic_league(seed=DEFAULT_SEED)
    distinct = np.unique(data.kickoff_date)
    cutoff = to_date(distinct[len(distinct) // 2])
    model = DixonColesModel(seed=DEFAULT_SEED)
    model.fit(history_before(data, cutoff), cutoff)
    return model, cutoff, data


def test_the_matrix_aggregates_to_the_prediction() -> None:
    model, cutoff, data = _fitted()
    fixtures = data.as_fixtures(data.kickoff_date >= np.datetime64(cutoff, "D"))

    matrix = model.score_matrix(fixtures)
    probabilities = model.predict(fixtures)

    assert len(fixtures) > 0
    assert matrix.shape == (len(fixtures), _GRID, _GRID)
    assert probabilities.shape == (len(fixtures), 3)
    aggregated = np.stack(
        [
            np.tril(matrix, -1).sum(axis=(1, 2)),
            np.diagonal(matrix, axis1=1, axis2=2).sum(axis=1),
            np.triu(matrix, 1).sum(axis=(1, 2)),
        ],
        axis=1,
    )
    np.testing.assert_allclose(aggregated, probabilities, rtol=0.0, atol=_TOLERANCE)


def test_every_matrix_is_a_distribution() -> None:
    model, _cutoff, _data = _fitted()
    fixtures = make_fixtures(3)

    matrix = model.score_matrix(fixtures)

    assert matrix.shape == (len(fixtures), _GRID, _GRID)
    assert bool((matrix >= 0.0).all())
    np.testing.assert_allclose(
        matrix.sum(axis=(1, 2)), np.ones(len(fixtures)), rtol=0.0, atol=_TOLERANCE
    )


def test_a_history_is_rejected() -> None:
    model, cutoff, data = _fitted()

    with pytest.raises(LeakageError, match="History"):
        model.score_matrix(history_before(data, cutoff))


def test_fixtures_before_the_cutoff_are_rejected() -> None:
    model, cutoff, _data = _fitted()
    earlier = make_fixtures(2, kickoff_date=cutoff - datetime.timedelta(days=1))

    with pytest.raises(LeakageError, match="before the cutoff"):
        model.score_matrix(earlier)


def test_a_call_before_fit_is_an_error() -> None:
    model = DixonColesModel(seed=DEFAULT_SEED)

    with pytest.raises(RuntimeError, match="before fit"):
        model.score_matrix(make_fixtures(2))
