"""Walk-forward windows: fit strictly before a cutoff, score the next window.

This module is the only place that slices a :class:`~lmi_core.modeling.types.MatchData`
by date. Every window constructs a fresh model, fits it on the matches dated
strictly before the cutoff and predicts the fixtures dated in
``[cutoff, cutoff + step_days)``. The leakage guards in
:class:`~lmi_core.modeling.base.Model` enforce the two boundaries, so a mistake
here surfaces as a ``LeakageError`` instead of a quietly optimistic score.
"""

import datetime
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from lmi_core.modeling.registry import make_model
from lmi_core.modeling.types import OUTCOMES, MatchData

DEFAULT_STEP_DAYS = 7
"""Length of one scoring window in days; weekly cutoffs leave no gap by default."""


@dataclass(frozen=True)
class WindowPredictions:
    """The forecasts of one walk-forward run, concatenated in window order."""

    match_id: NDArray[np.int64]
    probabilities: NDArray[np.float64]
    """One H/D/A row per match; an all-NaN row means the model had no forecast."""
    result: NDArray[np.int8]
    season_id: NDArray[np.int64]

    def __len__(self) -> int:
        """Return the number of scored rows."""
        return int(self.match_id.size)


def weekly_cutoffs(first: datetime.date, last: datetime.date) -> list[datetime.date]:
    """Return every Monday from the Monday on or before ``first`` through ``last``.

    The list ascends and is empty when ``first`` is after ``last``. A ``first``
    that is already a Monday is the first cutoff.
    """
    if first > last:
        return []
    cutoff = first - datetime.timedelta(days=first.weekday())
    step = datetime.timedelta(days=DEFAULT_STEP_DAYS)
    cutoffs: list[datetime.date] = []
    while cutoff <= last:
        cutoffs.append(cutoff)
        cutoff += step
    return cutoffs


def walk_forward(
    data: MatchData,
    name: str,
    *,
    seed: int,
    cutoffs: Sequence[datetime.date],
    step_days: int = DEFAULT_STEP_DAYS,
) -> WindowPredictions:
    """Fit and predict once per cutoff, concatenating the windows in the given order.

    A cutoff whose window holds no fixture contributes nothing. The history is
    ``data`` restricted to matches dated strictly before the cutoff and the
    fixtures are the rows of the window, with no outcome columns.

    Raises:
        ValueError: ``step_days`` is below 1, or the cutoffs are not strictly
            ascending.
        LeakageError: a window would let a model see a result dated on or after
            its cutoff.
    """
    if step_days < 1:
        raise ValueError(f"step_days must be at least 1, got {step_days}")
    _require_strictly_ascending(cutoffs)
    window = datetime.timedelta(days=step_days)

    match_ids: list[NDArray[np.int64]] = []
    probability_blocks: list[NDArray[np.float64]] = []
    results: list[NDArray[np.int8]] = []
    season_ids: list[NDArray[np.int64]] = []

    for cutoff in cutoffs:
        start = np.datetime64(cutoff, "D")
        end = np.datetime64(cutoff + window, "D")
        in_window = (data.kickoff_date >= start) & (data.kickoff_date < end)
        if not bool(in_window.any()):
            continue
        model = make_model(name, seed=seed)
        model.fit(data.select(data.kickoff_date < start), cutoff)
        probabilities = model.predict(data.as_fixtures(in_window))
        match_ids.append(data.match_id[in_window])
        probability_blocks.append(probabilities)
        results.append(data.result[in_window])
        season_ids.append(data.season_id[in_window])

    if not match_ids:
        return WindowPredictions(
            match_id=np.empty(0, dtype=np.int64),
            probabilities=np.empty((0, len(OUTCOMES)), dtype=np.float64),
            result=np.empty(0, dtype=np.int8),
            season_id=np.empty(0, dtype=np.int64),
        )
    return WindowPredictions(
        match_id=np.concatenate(match_ids),
        probabilities=np.concatenate(probability_blocks, axis=0),
        result=np.concatenate(results),
        season_id=np.concatenate(season_ids),
    )


def _require_strictly_ascending(cutoffs: Sequence[datetime.date]) -> None:
    """Raise when a cutoff repeats or moves backwards."""
    previous: datetime.date | None = None
    for cutoff in cutoffs:
        if previous is not None and cutoff <= previous:
            raise ValueError(
                "cutoffs must be strictly ascending: "
                f"{previous.isoformat()} is followed by {cutoff.isoformat()}"
            )
        previous = cutoff
