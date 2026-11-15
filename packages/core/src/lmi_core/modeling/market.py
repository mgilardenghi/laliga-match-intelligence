"""The market model: the probabilities the bookmaker odds imply, margin removed.

Decimal odds embed the bookmaker's margin: the implied probabilities of one row
sum to more than 1. The proportional method removes it by normalizing the
inverse prices, which is the standard reading of "the market expects this" and
needs no assumption about how the margin is distributed across the outcomes.

The model itself has nothing to estimate. It is a model rather than a baseline
because it is the reference every other model has to beat, and it reports no
forecast when a fixture carries no odds. Which bookmaker fills ``odds`` is the
loader's decision; this module never reads ``odds_source``.
"""

import datetime
from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray

from lmi_core.modeling.base import Model
from lmi_core.modeling.types import OUTCOMES, Fixtures, History


def remove_margin(odds: NDArray[np.float64]) -> NDArray[np.float64]:
    """Return the margin-removed implied probabilities of decimal ``H/D/A`` odds.

    Each row is normalized separately: ``p_i = (1 / o_i) / sum_j (1 / o_j)``, so
    every returned row sums to 1. A row with any NaN stays an all-NaN row: it
    carries no odds, and an all-NaN forecast row is how the model reports that it
    has no forecast for that fixture.

    Args:
        odds: Decimal odds, shape ``(n, 3)``, in ``H/D/A`` order.

    Returns:
        Probabilities, shape ``(n, 3)``, in the same order.

    Raises:
        ValueError: the odds do not have shape ``(n, 3)``, or a row that carries
            odds holds a price that is not positive and finite. Odds come from a
            loader, so a broken price is reported instead of being turned into a
            silent missing forecast.
    """
    decimal = np.asarray(odds, dtype=np.float64)
    if decimal.ndim != 2 or decimal.shape[1] != len(OUTCOMES):
        raise ValueError(f"odds must have shape (n, {len(OUTCOMES)}), got {decimal.shape}")
    carries_odds = ~np.isnan(decimal).any(axis=1)
    priceable = (decimal > 0.0) & np.isfinite(decimal)
    broken = carries_odds & ~priceable.all(axis=1)
    if bool(broken.any()):
        row = int(np.flatnonzero(broken)[0])
        raise ValueError(
            f"row {row} of the odds is not a positive finite decimal price: {decimal[row].tolist()}"
        )
    probabilities = np.full(decimal.shape, np.nan, dtype=np.float64)
    inverse = 1.0 / decimal[carries_odds]
    probabilities[carries_odds] = inverse / inverse.sum(axis=1, keepdims=True)
    return probabilities


class MarketModel(Model):
    """Predict the probabilities implied by the margin-removed bookmaker odds.

    ``fit`` is a no-op: the odds of a fixture are pre-kickoff information, so the
    model needs no history. A fixture without odds gets an all-NaN forecast row.
    """

    name = "market"
    version = "1"

    @property
    def hyperparameters(self) -> Mapping[str, object]:
        """The model has no tuned value; the seed is recorded for reproducibility."""
        return {"seed": self.seed}

    def _fit(self, history: History, cutoff: datetime.date) -> None:
        """Nothing to estimate: the odds are the forecast."""

    def _predict(self, fixtures: Fixtures) -> NDArray[np.float64]:
        return remove_margin(fixtures.odds)
