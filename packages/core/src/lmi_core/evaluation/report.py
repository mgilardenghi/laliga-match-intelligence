"""What a backtest reports: per-model scores, calibration bins and the run identity.

This module is pure data. It holds frozen dataclasses with no validation and no
numeric dependency, so the runner can build a report and the API can serialize
one without either side importing NumPy. The runner validates the rows it
collects; these types only carry them.

The numbers in a :class:`ScoreRow` come from ``lmi_core.evaluation.metrics``;
its ``EPS`` probability clip is what keeps a certain-against forecast finite.
"""

from dataclasses import dataclass
from typing import Literal

Subset = Literal["all", "common"]
"""The matches a row covers: every scored match, or only the common set."""


@dataclass(frozen=True)
class ScoreRow:
    """The metrics of one model over one season (or all seasons) and one subset."""

    model: str
    season_id: int | None
    """Scored season, or ``None`` when the row covers every scored season."""
    subset: Subset
    n_matches: int
    """Matches behind the metrics: a forecast exists and the result is known."""
    n_fixtures: int
    """Fixtures in the covered window, including those the model did not forecast."""
    log_loss: float
    brier: float
    rps: float
    accuracy: float


@dataclass(frozen=True)
class CalibrationRow:
    """One equal-width probability bin for one model and one outcome."""

    model: str
    outcome: Literal["H", "D", "A"]
    bin_index: int
    bin_lower: float
    bin_upper: float
    n: int
    """Rows whose forecast for ``outcome`` falls in the bin."""
    mean_predicted: float | None
    """Mean forecast inside the bin; ``None`` exactly when ``n == 0``."""
    observed_frequency: float | None
    """Share of those rows whose outcome is ``outcome``; ``None`` exactly when ``n == 0``."""


@dataclass(frozen=True)
class BacktestConfig:
    """The knobs that make a backtest reproducible."""

    models: tuple[str, ...]
    from_year: int = 2007
    step_days: int = 7
    seed: int = 42


@dataclass(frozen=True)
class BacktestReport:
    """One backtest run: its configuration, its rows and its input fingerprint."""

    config: BacktestConfig
    scores: list[ScoreRow]
    calibration: list[CalibrationRow]
    data_fingerprint: str
    model_descriptions: dict[str, dict[str, object]]
