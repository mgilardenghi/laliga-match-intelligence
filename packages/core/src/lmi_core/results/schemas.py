"""Pydantic DTOs for the backtest results contract.

:class:`ModelMetricsResponse` is the single JSON shape shared by three callers:
the results store (:mod:`lmi_core.results.store`), the future
``GET /v1/model-metrics`` endpoint and the worker's ``--format json`` output.
The field names, the optional identity fields and the numeric ranges therefore
change only together.

The ranges mirror the CHECK constraints of the ``backtest_*`` tables, so a row
the database accepts also validates here. ``coverage`` is the exception: it is
derived from ``n_matches / n_fixtures`` and stored nowhere.

The model names are declared twice on purpose: :data:`BACKTEST_MODEL_NAMES`
fixes the report order and :data:`ModelName` is the literal every DTO uses.
Both must stay identical to the names in the database models module and in the
modeling registry. This package must not import the modeling package, so the
consistency test across the three lists belongs to a later task.
"""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

BACKTEST_MODEL_NAMES: tuple[str, ...] = (
    "home_win",
    "base_rate",
    "market",
    "elo",
    "dixon_coles",
)
"""The five scored models, in report order."""

ModelName = Literal["home_win", "base_rate", "market", "elo", "dixon_coles"]
"""One scored model name, listing exactly :data:`BACKTEST_MODEL_NAMES`."""

ModelKind = Literal["baseline", "market", "model"]
"""The role a model plays in the report."""

Subset = Literal["all", "common"]
"""The scored match set: every forecast, or the intersection across models."""

Outcome = Literal["H", "D", "A"]
"""The result order of the calibration rows, matching the model output order."""


class BacktestRunOut(BaseModel):
    """The header of one backtest run.

    ``id``, ``started_at`` and ``finished_at`` are null only in the worker's
    ``--format json --no-save`` output, where no run row exists. A stored run
    always carries all three.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    seed: int
    from_year: int
    step_days: int = Field(ge=1)
    code_version: str
    data_fingerprint: str
    n_matches: int = Field(ge=0)


class ModelInfo(BaseModel):
    """One model of a run, as ``describe()`` reports it.

    The hyperparameters are recorded next to the metrics so a reader can tell
    which settings produced them. ``version`` is bumped whenever a
    hyperparameter or the math changes.
    """

    model_config = ConfigDict(extra="forbid")

    name: ModelName
    kind: ModelKind
    version: str
    hyperparameters: dict[str, float | int | str]


class MetricRow(BaseModel):
    """The metrics of one model on one season or on all scored seasons.

    ``season_code`` is ``seasons.code``; ``None`` is the overall row.
    ``coverage`` is ``n_matches / n_fixtures``; ``load_report`` computes it, so
    a stored row does not carry it.
    """

    model_config = ConfigDict(extra="forbid")

    model: ModelName
    season_code: str | None
    subset: Subset
    n_matches: int = Field(gt=0)
    n_fixtures: int = Field(ge=0)
    coverage: float = Field(ge=0, le=1)
    log_loss: float = Field(ge=0)
    brier: float = Field(ge=0, le=2)
    rps: float = Field(ge=0, le=1)
    accuracy: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _fixtures_cover_matches(self) -> Self:
        """Reject a row that forecasts more matches than its fixture scope."""
        if self.n_fixtures < self.n_matches:
            raise ValueError("n_fixtures must be at least n_matches")
        return self


class CalibrationBin(BaseModel):
    """One equal-width calibration bin of one model and outcome.

    An empty bin (``n = 0``) has no mean prediction and no observed frequency;
    the database stores that as two NULLs and this DTO states the same rule.
    """

    model_config = ConfigDict(extra="forbid")

    model: ModelName
    outcome: Outcome
    bin_index: int = Field(ge=0, le=99)
    bin_lower: float
    bin_upper: float
    n: int = Field(ge=0)
    mean_predicted: float | None
    observed_frequency: float | None

    @model_validator(mode="after")
    def _empty_bin_has_no_values(self) -> Self:
        """Reject a bin whose counts and summary values contradict each other."""
        if (self.n == 0) != (self.mean_predicted is None):
            raise ValueError("an empty calibration bin must have mean_predicted = null")
        if (self.n == 0) != (self.observed_frequency is None):
            raise ValueError("an empty calibration bin must have observed_frequency = null")
        return self


class ModelMetricsResponse(BaseModel):
    """One backtest run and its results, ordered per the API contract.

    The lists are ordered by the producer: models in :data:`BACKTEST_MODEL_NAMES`
    order, metrics by season and then ``all`` before ``common``, and
    calibration by model, outcome and bin index.
    """

    model_config = ConfigDict(extra="forbid")

    run: BacktestRunOut
    models: list[ModelInfo]
    metrics: list[MetricRow]
    calibration: list[CalibrationBin]
