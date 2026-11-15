"""Scores for H/D/A probability forecasts, one match at a time.

``probabilities`` is a ``(n, 3)`` array of H, D, A rows and ``result`` holds the
outcome index per row (``0`` = H, ``1`` = D, ``2`` = A, as
``History.result`` produces). Every metric is the mean over the rows that carry
a forecast: a row that is NaN in all three columns means the model had no
forecast for that match and is dropped before anything is computed, so a sparse
model is compared on the matches it did forecast. An input with no surviving
row raises instead of returning a NaN or a meaningless zero.

Probabilities are not re-validated here beyond shape and NaN layout: the models
check their own output and the runner checks what it stores.
"""

from typing import Final, Literal

import numpy as np
from numpy.typing import NDArray

from lmi_core.evaluation.report import CalibrationRow

EPS = 1e-15
"""Probability clip used by :func:`log_loss`, so a certain-against forecast stays finite."""

_OUTCOMES: Final[tuple[Literal["H", "D", "A"], ...]] = ("H", "D", "A")
_COLUMNS: Final = len(_OUTCOMES)
_NO_FORECAST: Final = "no forecast to score"


def one_hot(result: NDArray[np.int8]) -> NDArray[np.float64]:
    """Return the ``(n, 3)`` one-hot encoding of ``result`` in H, D, A order."""
    return _one_hot(_labels(result))


def log_loss(probabilities: NDArray[np.float64], result: NDArray[np.int8]) -> float:
    """Return ``-mean(log(clip(p[result], EPS, 1)))`` over the rows with a forecast."""
    scored, labels = _scored(probabilities, result)
    chosen = scored[np.arange(scored.shape[0]), labels]
    return float(-np.mean(np.log(np.clip(chosen, EPS, 1.0))))


def brier(probabilities: NDArray[np.float64], result: NDArray[np.int8]) -> float:
    """Return ``mean(sum_k (p_k - y_k)**2)`` over the rows with a forecast, in ``[0, 2]``."""
    scored, labels = _scored(probabilities, result)
    return float(np.mean(np.sum((scored - _one_hot(labels)) ** 2, axis=1)))


def rps(probabilities: NDArray[np.float64], result: NDArray[np.int8]) -> float:
    """Return ``mean(sum_{k=1..2} (P_k - Y_k)**2 / 2)`` on the cumulative H, D, A sums."""
    scored, labels = _scored(probabilities, result)
    cumulative_forecast = np.cumsum(scored, axis=1)[:, : _COLUMNS - 1]
    cumulative_outcome = np.cumsum(_one_hot(labels), axis=1)[:, : _COLUMNS - 1]
    squared = (cumulative_forecast - cumulative_outcome) ** 2
    return float(np.mean(np.sum(squared, axis=1) / 2.0))


def accuracy(probabilities: NDArray[np.float64], result: NDArray[np.int8]) -> float:
    """Return ``mean(argmax(p) == result)``, with ties broken in H, D, A order."""
    scored, labels = _scored(probabilities, result)
    return float(np.mean(np.argmax(scored, axis=1) == labels))


def calibration_bins(
    probabilities: NDArray[np.float64],
    result: NDArray[np.int8],
    *,
    model: str,
    n_bins: int = 10,
) -> list[CalibrationRow]:
    """Bin the forecast per outcome over ``n_bins`` equal widths in H, D, A, low to high.

    A forecast ``p`` lands in bin ``min(int(p * n_bins), n_bins - 1)``, so ``0.0``
    is in the first bin and ``1.0`` in the last one. A bin with ``n == 0``
    reports ``None`` for its mean forecast and its observed frequency.
    """
    if n_bins < 1:
        raise ValueError(f"n_bins must be at least 1, got {n_bins}")
    scored, labels = _scored(probabilities, result)
    rows: list[CalibrationRow] = []
    for column, outcome in enumerate(_OUTCOMES):
        predicted = scored[:, column]
        hit = labels == column
        index = np.minimum((predicted * n_bins).astype(np.intp), n_bins - 1)
        for bin_index in range(n_bins):
            inside = index == bin_index
            n = int(np.count_nonzero(inside))
            rows.append(
                CalibrationRow(
                    model=model,
                    outcome=outcome,
                    bin_index=bin_index,
                    bin_lower=bin_index / n_bins,
                    bin_upper=(bin_index + 1) / n_bins,
                    n=n,
                    mean_predicted=float(np.mean(predicted[inside])) if n else None,
                    observed_frequency=float(np.mean(hit[inside])) if n else None,
                )
            )
    return rows


def _one_hot(labels: NDArray[np.intp]) -> NDArray[np.float64]:
    """Return the one-hot encoding of flat outcome indices."""
    encoded = np.zeros((labels.size, _COLUMNS), dtype=np.float64)
    encoded[np.arange(labels.size), labels] = 1.0
    return encoded


def _labels(result: NDArray[np.int8]) -> NDArray[np.intp]:
    """Return the outcome indices as a flat index array."""
    labels = np.asarray(result)
    if labels.ndim != 1:
        raise ValueError(f"result must be one-dimensional, got shape {labels.shape}")
    return labels.astype(np.intp)


def _scored(
    probabilities: NDArray[np.float64], result: NDArray[np.int8]
) -> tuple[NDArray[np.float64], NDArray[np.intp]]:
    """Check the shapes and return only the rows that carry a forecast.

    Raises:
        ValueError: the shapes disagree, a row mixes NaN with real values, or no
            row carries a forecast.
    """
    forecast = np.asarray(probabilities, dtype=np.float64)
    if forecast.ndim != 2 or forecast.shape[1] != _COLUMNS:
        raise ValueError(f"probabilities must have shape (n, {_COLUMNS}), got {forecast.shape}")
    labels = _labels(result)
    if labels.size != forecast.shape[0]:
        raise ValueError(f"result must have {forecast.shape[0]} entries, got {labels.size}")
    missing = np.isnan(forecast)
    all_missing = missing.all(axis=1)
    partial = np.flatnonzero(missing.any(axis=1) & ~all_missing)
    if partial.size:
        raise ValueError(f"row {int(partial[0])} mixes NaN with real probabilities")
    scored = np.flatnonzero(~all_missing)
    if scored.size == 0:
        raise ValueError(_NO_FORECAST)
    return forecast[scored], labels[scored]
