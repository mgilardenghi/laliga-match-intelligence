"""Turn a backtest report into the one model-metrics contract.

This module is the producer shared by three callers: the worker CLI's
``--format json`` output, the run the store persists and the future
``GET /v1/model-metrics`` endpoint. They all hand a
:class:`~lmi_core.evaluation.report.BacktestReport` and the season codes they
already loaded to :func:`report_to_response` and serve the result, so no caller
invents a field, a row or an order of its own.

The conversion is pure, and the imports say so: the report is plain frozen
dataclasses, so this module reaches neither the modeling package, NumPy, the
backtest runner nor SQLAlchemy. A contract built here needs no database, no
model and no random source, which is what lets the API import it.

Two values are derived rather than carried:

* ``coverage`` is exactly ``n_matches / n_fixtures``. The store does not persist
  it and the DTO accepts no other value, so every producer must compute the same
  ratio from the same two counts.
* the display precision. A stored or served metric keeps every digit the run
  computed; :func:`round_response` exists for the human-readable output only,
  where a 17-digit float is noise. It never touches ``coverage``, because
  rounding that ratio is enough to make the response unbuildable.
"""

from collections.abc import Mapping, Sequence
from typing import Final, cast

from lmi_core.evaluation.report import BacktestReport, CalibrationRow, ScoreRow
from lmi_core.results.schemas import (
    BACKTEST_MODEL_NAMES,
    BacktestRunOut,
    CalibrationBin,
    MetricRow,
    ModelInfo,
    ModelMetricsResponse,
    ModelName,
)

_MODEL_ORDER: Final[dict[str, int]] = {
    name: index for index, name in enumerate(BACKTEST_MODEL_NAMES)
}
"""Report position of each model name; unknown names sort last."""

_SUBSET_ORDER: Final[dict[str, int]] = {"all": 0, "common": 1}
"""Report position of each subset: ``all`` before ``common``."""

_OUTCOME_ORDER: Final[dict[str, int]] = {"H": 0, "D": 1, "A": 2}
"""Report position of each outcome, matching the model output order."""


def report_to_response(
    report: BacktestReport,
    *,
    run: BacktestRunOut,
    models: Sequence[ModelInfo],
    season_codes: Mapping[int, str],
) -> ModelMetricsResponse:
    """Return the DTO of ``report``, ordered as the contract requires.

    The rows of the report are mapped 1:1 and reordered, never rewritten: the
    metric values pass through unchanged and ``coverage`` is derived from the
    two counts. The order is the contract's, not the report's, so a producer may
    emit its rows in any order:

    * ``models`` in :data:`BACKTEST_MODEL_NAMES` order, a name outside that
      tuple last and stable;
    * metrics by model, then season in the order the report first mentions it,
      then ``all`` before ``common``, with the overall row last;
    * calibration by model, then outcome ``H``, ``D``, ``A``, then bin index.

    The season order is the report's first-seen order because the report emits
    seasons chronologically while ``season_id`` values are assigned by the
    ingestion, so their numeric order need not follow the calendar.

    Args:
        report: the rows a backtest produced.
        run: the header of the run, with a null identity when no row exists.
        models: the models of the run, as ``describe()`` reports them.
        season_codes: ``seasons.id`` to ``seasons.code`` for every season the
            report scores.

    Returns:
        The validated DTO of the report.

    Raises:
        ValueError: the report scores a season id that ``season_codes`` does not
            contain. The message names the id.
        pydantic.ValidationError: a row violates the DTO contract, for example
            a metric row without a scored match.
    """
    return ModelMetricsResponse(
        run=run,
        models=sorted(models, key=lambda info: _model_order(info.name)),
        metrics=_metric_rows(report.scores, season_codes),
        calibration=_calibration_bins(report.calibration),
    )


def round_response(response: ModelMetricsResponse, *, digits: int = 6) -> ModelMetricsResponse:
    """Return a validated copy of ``response`` rounded to ``digits`` decimals.

    Only the display floats are rounded: the four metric values of a
    :class:`~lmi_core.results.schemas.MetricRow` and the four calibration values
    of a :class:`~lmi_core.results.schemas.CalibrationBin`. A ``None`` summary
    value of an empty bin stays ``None``.

    Every row is rebuilt through its pydantic model instead of being copied, so
    the result is validated like any produced response and a rounded value that
    left a range fails here instead of reaching a reader.

    Args:
        response: the response to round.
        digits: the number of decimals to keep.

    Returns:
        A new response whose display floats are rounded and whose counts,
        identity and order are unchanged.

    Raises:
        pydantic.ValidationError: a rounded value violates the DTO contract.
    """
    return ModelMetricsResponse(
        run=response.run,
        models=response.models,
        metrics=[
            MetricRow(
                model=row.model,
                season_code=row.season_code,
                subset=row.subset,
                n_matches=row.n_matches,
                n_fixtures=row.n_fixtures,
                # Not rounded. The DTO accepts exactly n_matches / n_fixtures
                # (COVERAGE_TOLERANCE is 1e-12), and rounding 87 / 90 to six
                # decimals already misses it by about 3e-7, so a rounded
                # coverage would make the response itself unbuildable.
                coverage=row.coverage,
                log_loss=round(row.log_loss, digits),
                brier=round(row.brier, digits),
                rps=round(row.rps, digits),
                accuracy=round(row.accuracy, digits),
            )
            for row in response.metrics
        ],
        calibration=[
            CalibrationBin(
                model=row.model,
                outcome=row.outcome,
                bin_index=row.bin_index,
                bin_lower=round(row.bin_lower, digits),
                bin_upper=round(row.bin_upper, digits),
                n=row.n,
                mean_predicted=_rounded(row.mean_predicted, digits),
                observed_frequency=_rounded(row.observed_frequency, digits),
            )
            for row in response.calibration
        ],
    )


def _metric_rows(scores: Sequence[ScoreRow], season_codes: Mapping[int, str]) -> list[MetricRow]:
    """Return one DTO row per score row, in the contract order."""
    seasons: list[int] = []
    for row in scores:
        if row.season_id is not None and row.season_id not in seasons:
            seasons.append(row.season_id)
    season_order = {season_id: index for index, season_id in enumerate(seasons)}
    ordered = sorted(
        scores,
        key=lambda row: (
            _model_order(row.model),
            len(seasons) if row.season_id is None else season_order[row.season_id],
            _SUBSET_ORDER.get(row.subset, len(_SUBSET_ORDER)),
        ),
    )
    return [_metric_row(row, season_codes) for row in ordered]


def _metric_row(row: ScoreRow, season_codes: Mapping[int, str]) -> MetricRow:
    """Return the DTO row of one score row."""
    return MetricRow(
        model=cast(ModelName, row.model),
        season_code=_season_code(row.season_id, season_codes),
        subset=row.subset,
        n_matches=row.n_matches,
        n_fixtures=row.n_fixtures,
        # Derived, never stored: the DTO defines coverage as this exact ratio.
        coverage=row.n_matches / row.n_fixtures,
        log_loss=row.log_loss,
        brier=row.brier,
        rps=row.rps,
        accuracy=row.accuracy,
    )


def _season_code(season_id: int | None, season_codes: Mapping[int, str]) -> str | None:
    """Return the season code of ``season_id``; ``None`` stays the overall row.

    Raises:
        ValueError: the mapping has no entry for ``season_id``. Dropping the row
            or inventing a code would serve a season code no reader can resolve.
    """
    if season_id is None:
        return None
    code = season_codes.get(season_id)
    if code is None:
        raise ValueError(f"unknown season id {season_id}: no season code was loaded for it")
    return code


def _calibration_bins(rows: Sequence[CalibrationRow]) -> list[CalibrationBin]:
    """Return one DTO bin per calibration row, in the contract order."""
    ordered = sorted(
        rows,
        key=lambda row: (
            _model_order(row.model),
            _OUTCOME_ORDER.get(row.outcome, len(_OUTCOME_ORDER)),
            row.bin_index,
        ),
    )
    return [
        CalibrationBin(
            model=cast(ModelName, row.model),
            outcome=row.outcome,
            bin_index=row.bin_index,
            bin_lower=row.bin_lower,
            bin_upper=row.bin_upper,
            n=row.n,
            mean_predicted=row.mean_predicted,
            observed_frequency=row.observed_frequency,
        )
        for row in ordered
    ]


def _rounded(value: float | None, digits: int) -> float | None:
    """Return ``value`` rounded to ``digits``; an absent value stays absent."""
    return None if value is None else round(value, digits)


def _model_order(name: str) -> int:
    """Return the report position of ``name``; unknown names sort last."""
    return _MODEL_ORDER.get(name, len(BACKTEST_MODEL_NAMES))
