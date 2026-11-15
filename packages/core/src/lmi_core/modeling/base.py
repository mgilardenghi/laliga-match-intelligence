"""The common model interface with the leakage guards every model inherits.

``fit`` and ``predict`` are the public API and both are ``@final``: they apply
the leakage guards, and a subclass only implements :meth:`Model._fit` and
:meth:`Model._predict`. The boundary is the calendar date ``cutoff``: history is
``kickoff_date < cutoff`` and fixtures are ``kickoff_date >= cutoff``, so a match
played on the cutoff date is never visible to the fit.
"""

import datetime
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import ClassVar, final

import numpy as np
from numpy.typing import NDArray

from lmi_core.modeling.types import OUTCOMES, Fixtures, History, LeakageError

_ROW_SUM_TOLERANCE = 1e-9


class Model(ABC):
    """Base class for every model.

    Subclasses are constructed with a seed, implement ``_fit`` and ``_predict``,
    and inherit the guards unchanged. The seed is recorded for reproducibility;
    a stochastic component must draw from ``np.random.default_rng(self.seed)``
    and never from global state.
    """

    # Registry key of the model.
    name: ClassVar[str]

    # Bumped whenever the math or the hyperparameters change.
    version: ClassVar[str]

    def __init__(self, *, seed: int) -> None:
        self.seed = seed
        self._cutoff: datetime.date | None = None

    @property
    def hyperparameters(self) -> Mapping[str, object]:
        """Values fixed a priori and recorded next to each prediction."""
        return {}

    def describe(self) -> dict[str, object]:
        """Return the identity that is stored next to a stored prediction."""
        return {
            "name": self.name,
            "version": self.version,
            "hyperparameters": dict(self.hyperparameters),
        }

    @final
    def fit(self, history: History, cutoff: datetime.date) -> None:
        """Fit on played matches strictly before ``cutoff``."""
        cutoff_day = np.datetime64(cutoff, "D")
        leaked = history.kickoff_date >= cutoff_day
        if bool(leaked.any()):
            earliest = history.kickoff_date[leaked].min()
            raise LeakageError(
                f"{self.name}: fit history contains {int(leaked.sum())} of {len(history)} "
                f"matches dated on or after the cutoff {cutoff.isoformat()} "
                f"(earliest {earliest}); history must be strictly before the cutoff"
            )
        self._cutoff = cutoff
        self._fit(history, cutoff)

    @final
    def predict(self, fixtures: Fixtures) -> NDArray[np.float64]:
        """Return one H/D/A probability row per fixture.

        Raises:
            RuntimeError: :meth:`fit` has not been called yet.
            LeakageError: a ``History`` was passed instead of fixtures, or a
                fixture is dated before the cutoff the model was fitted at.
            ValueError: the subclass returned probabilities that are not a
                ``(n, 3)`` array of all-NaN or non-negative rows summing to 1.
        """
        self._guard_fixtures(fixtures)
        probabilities = np.asarray(self._predict(fixtures), dtype=np.float64)
        _validate_probabilities(probabilities, expected_rows=len(fixtures), model_name=self.name)
        return probabilities

    @final
    def _guard_fixtures(self, fixtures: Fixtures) -> None:
        """Apply the input guards every prediction path shares.

        The checks run in this order: the model is fitted, the argument is not a
        ``History``, and every fixture is dated on or after the cutoff the fit
        used.

        Raises:
            RuntimeError: :meth:`fit` has not been called yet.
            LeakageError: a ``History`` was passed instead of fixtures, or a
                fixture is dated before the cutoff the model was fitted at.
        """
        if self._cutoff is None:
            raise RuntimeError(f"{self.name}: predict() called before fit()")
        if isinstance(fixtures, History):
            raise LeakageError(
                f"{self.name}: predict() received a History, which carries goals and results; "
                "pass History.as_fixtures(mask) instead"
            )
        cutoff_day = np.datetime64(self._cutoff, "D")
        leaked = fixtures.kickoff_date < cutoff_day
        if bool(leaked.any()):
            latest = fixtures.kickoff_date[leaked].max()
            raise LeakageError(
                f"{self.name}: fixtures contain {int(leaked.sum())} of {len(fixtures)} "
                f"matches dated before the cutoff {self._cutoff.isoformat()} "
                f"(latest {latest}); a fixture must be on or after the cutoff"
            )

    @abstractmethod
    def _fit(self, history: History, cutoff: datetime.date) -> None:
        """Store whatever the model needs from ``history``."""

    @abstractmethod
    def _predict(self, fixtures: Fixtures) -> NDArray[np.float64]:
        """Return one probability row per fixture, without applying the guards."""


def _validate_probabilities(
    probabilities: NDArray[np.float64], *, expected_rows: int, model_name: str
) -> None:
    """Check the contract of :meth:`Model._predict`."""
    expected_shape = (expected_rows, len(OUTCOMES))
    if probabilities.shape != expected_shape:
        raise ValueError(
            f"{model_name}: probabilities must have shape {expected_shape}, "
            f"got {probabilities.shape}"
        )
    missing = np.isnan(probabilities)
    all_missing = missing.all(axis=1)
    partial = missing.any(axis=1) & ~all_missing
    if bool(partial.any()):
        row = int(np.flatnonzero(partial)[0])
        raise ValueError(f"{model_name}: row {row} mixes NaN with real probabilities")
    inside = ~all_missing
    filled = np.where(np.isnan(probabilities), 0.0, probabilities)
    negative_mask = (filled < 0).any(axis=1) & inside
    negative = np.flatnonzero(negative_mask)
    if negative.size:
        row = int(negative[0])
        raise ValueError(f"{model_name}: row {row} of the forecasts is negative")
    totals = filled.sum(axis=1)
    off_sum = np.flatnonzero((np.abs(totals - 1.0) > _ROW_SUM_TOLERANCE) & inside)
    if off_sum.size:
        row = int(off_sum[0])
        raise ValueError(f"{model_name}: row {row} of the forecasts sums to {float(totals[row])}")
