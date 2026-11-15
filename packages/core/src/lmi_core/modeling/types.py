"""Columnar data types that mark the boundary between fitting and predicting.

A model is fitted on :class:`History` (played matches, with goals and result) and
predicts on :class:`Fixtures` (matches still to play, with no outcome columns).
The split is structural: ``Fixtures`` has no ``result`` attribute, so a model
cannot read an outcome it was not allowed to see. ``History`` subclasses
``Fixtures`` so the shared columns are declared once; that is why
:meth:`lmi_core.modeling.base.Model.predict` rejects a ``History`` at runtime.

Every array is indexed the same way, and rows are sorted by
``(kickoff_date, match_id)`` so that a date slice is a contiguous range.
"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

OUTCOMES = ("H", "D", "A")
"""Outcomes in the order used by ``result`` and by every probability row."""

_HOME = 0
_DRAW = 1
_AWAY = 2

_FIXTURE_FIELDS = ("match_id", "kickoff_date", "season_id", "home_team_id", "away_team_id")
_HISTORY_FIELDS = ("home_goals", "away_goals", "result")


class LeakageError(RuntimeError):
    """Raised when information from the future would reach a model."""


@dataclass(frozen=True)
class Fixtures:
    """What a model may see about the matches it has to predict."""

    match_id: NDArray[np.int64]
    kickoff_date: NDArray[np.datetime64]
    season_id: NDArray[np.int64]
    home_team_id: NDArray[np.int64]
    away_team_id: NDArray[np.int64]
    odds: NDArray[np.float64]
    """Decimal H/D/A odds, shape ``(n, 3)``; an all-NaN row means no odds."""
    odds_source: tuple[str | None, ...]
    """Bookmaker label used per row, ``None`` when the row carries no odds."""

    def __post_init__(self) -> None:
        _validate_common(self)

    def __len__(self) -> int:
        """Return the number of rows."""
        return int(self.match_id.size)


@dataclass(frozen=True)
class History(Fixtures):
    """Played matches, with the outcome a model is fitted on."""

    home_goals: NDArray[np.int64]
    away_goals: NDArray[np.int64]
    result: NDArray[np.int8]
    """Outcome index into :data:`OUTCOMES`: ``0`` = H, ``1`` = D, ``2`` = A."""

    def __post_init__(self) -> None:
        _validate_common(self)
        _validate_outcomes(self)

    def select(self, mask: NDArray[np.bool_]) -> History:
        """Return the rows where ``mask`` is true."""
        selection = _as_mask(mask, len(self))
        return History(
            match_id=self.match_id[selection],
            kickoff_date=self.kickoff_date[selection],
            season_id=self.season_id[selection],
            home_team_id=self.home_team_id[selection],
            away_team_id=self.away_team_id[selection],
            odds=self.odds[selection],
            odds_source=_select_sources(self.odds_source, selection),
            home_goals=self.home_goals[selection],
            away_goals=self.away_goals[selection],
            result=self.result[selection],
        )

    def as_fixtures(self, mask: NDArray[np.bool_]) -> Fixtures:
        """Return the rows where ``mask`` is true, without any outcome column."""
        selection = _as_mask(mask, len(self))
        return Fixtures(
            match_id=self.match_id[selection],
            kickoff_date=self.kickoff_date[selection],
            season_id=self.season_id[selection],
            home_team_id=self.home_team_id[selection],
            away_team_id=self.away_team_id[selection],
            odds=self.odds[selection],
            odds_source=_select_sources(self.odds_source, selection),
        )


MatchData = History
"""The full played set the loader returns; the alias exists for readability."""


def _as_mask(mask: NDArray[np.bool_], n: int) -> NDArray[np.bool_]:
    """Normalize a selection mask and check that it covers exactly ``n`` rows."""
    selection = np.asarray(mask, dtype=bool)
    if selection.shape != (n,):
        raise ValueError(f"a selection mask must have shape ({n},), got {selection.shape}")
    return selection


def _select_sources(
    odds_source: tuple[str | None, ...], selection: NDArray[np.bool_]
) -> tuple[str | None, ...]:
    return tuple(source for source, keep in zip(odds_source, selection, strict=True) if keep)


def _validate_common(data: Fixtures) -> None:
    """Check the rules every columnar type shares."""
    _validate_lengths(data)
    n = len(data)
    if data.odds.shape != (n, 3):
        raise ValueError(f"odds must have shape ({n}, 3), got {data.odds.shape}")
    if len(data.odds_source) != n:
        raise ValueError(f"odds_source must have {n} entries, got {len(data.odds_source)}")
    _validate_order(data)


def _validate_lengths(data: Fixtures) -> None:
    fields = _FIXTURE_FIELDS + (_HISTORY_FIELDS if isinstance(data, History) else ())
    n = int(data.match_id.size)
    lengths = {field: int(getattr(data, field).size) for field in fields}
    if any(length != n for length in lengths.values()):
        raise ValueError(f"all fields must have the same length, got {lengths}")


def _validate_order(data: Fixtures) -> None:
    n = len(data)
    if n < 2:
        return
    order = np.lexsort((data.match_id, data.kickoff_date))
    if not np.array_equal(order, np.arange(n)):
        raise ValueError("rows must be sorted by (kickoff_date, match_id)")


def _validate_outcomes(data: History) -> None:
    expected = _result_from_goals(data.home_goals, data.away_goals)
    mismatched = np.flatnonzero(data.result != expected)
    if mismatched.size:
        row = int(mismatched[0])
        raise ValueError(
            "result must agree with home_goals and away_goals: row "
            f"{row} is {int(data.home_goals[row])}-{int(data.away_goals[row])} "
            f"but result is {int(data.result[row])}"
        )


def _result_from_goals(
    home_goals: NDArray[np.int64], away_goals: NDArray[np.int64]
) -> NDArray[np.int8]:
    """Map a scoreline to the ``OUTCOMES`` index."""
    result = np.where(
        home_goals > away_goals, _HOME, np.where(home_goals == away_goals, _DRAW, _AWAY)
    )
    return result.astype(np.int8)
