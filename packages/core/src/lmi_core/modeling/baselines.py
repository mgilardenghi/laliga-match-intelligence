"""The baselines: always-home-win, and the outcome frequencies of the history.

``home_win`` is the spec's baseline. Its log loss is degenerate by construction:
without any information the home side gets probability 1, so every match that
does not end in a home win scores ``-ln(EPS)`` under the global clip
``EPS = 1e-15``. The report states the value so it reads as intended rather than
as a bug. ``base_rate`` is the non-degenerate reference: it predicts the H/D/A
frequencies of its own training window, so it says what a model has to beat
before any match feature is worth its cost.
"""

import datetime

import numpy as np
from numpy.typing import NDArray

from lmi_core.modeling.base import Model
from lmi_core.modeling.types import OUTCOMES, Fixtures, History

_HOME = 0
_UNIFORM = 1.0 / len(OUTCOMES)


class HomeWinModel(Model):
    """Predict the home win with certainty, for every fixture."""

    name = "home_win"
    version = "1"

    def _fit(self, history: History, cutoff: datetime.date) -> None:
        """Nothing to estimate: the baseline has no hyperparameters."""

    def _predict(self, fixtures: Fixtures) -> NDArray[np.float64]:
        probabilities = np.zeros((len(fixtures), len(OUTCOMES)), dtype=np.float64)
        probabilities[:, _HOME] = 1.0
        return probabilities


class BaseRateModel(Model):
    """Predict the H/D/A frequencies observed strictly before the cutoff."""

    name = "base_rate"
    version = "1"

    def __init__(self, *, seed: int) -> None:
        super().__init__(seed=seed)
        self._frequencies = np.full(len(OUTCOMES), _UNIFORM, dtype=np.float64)

    def _fit(self, history: History, cutoff: datetime.date) -> None:
        """Store the outcome frequencies of ``history``, uniform when it is empty."""
        counts = np.bincount(history.result.astype(np.intp), minlength=len(OUTCOMES))
        total = int(counts.sum())
        if total == 0:
            self._frequencies = np.full(len(OUTCOMES), _UNIFORM, dtype=np.float64)
        else:
            self._frequencies = counts.astype(np.float64) / total

    def _predict(self, fixtures: Fixtures) -> NDArray[np.float64]:
        return np.tile(self._frequencies, (len(fixtures), 1))
