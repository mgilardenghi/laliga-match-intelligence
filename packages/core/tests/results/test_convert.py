"""Tests for the converter from a backtest report to the model-metrics DTO.

The report comes from the real runner over the synthetic league, so the rows,
their numbers and their order are the ones the CLI converts. The tests pin the
three things this module owns: the 1:1 mapping into the DTO, the contract order
(which must hold whatever order the report rows are in) and the display-only
rounding, which must leave the derived ``coverage`` exact.
"""

from dataclasses import replace
from typing import cast

import pytest
from lmi_core.evaluation.backtest import run_backtest
from lmi_core.evaluation.report import BacktestConfig, BacktestReport
from lmi_core.modeling.registry import MODEL_KINDS
from lmi_core.modeling.types import MatchData
from lmi_core.results.convert import report_to_response, round_response
from lmi_core.results.schemas import (
    BACKTEST_MODEL_NAMES,
    BacktestRunOut,
    ModelInfo,
    ModelMetricsResponse,
    ModelName,
    Subset,
)
from modeling.leakage_harness import make_synthetic_league

MODELS = ("home_win", "base_rate")
"""The models the synthetic backtest scores."""

SEASON_CODES: dict[int, str] = {1: "2005-06", 2: "2006-07", 3: "2007-08"}
"""The season codes of the synthetic league, keyed by its season ids."""

SUBSETS: tuple[Subset, ...] = ("all", "common")
OUTCOMES = ("H", "D", "A")
DISPLAY_DECIMALS = 6


def test_every_report_row_becomes_a_validated_dto_row() -> None:
    data = make_synthetic_league()
    report = run_backtest(data, BacktestConfig(models=MODELS))

    response = _response(report, data, models=list(reversed(_models())))

    assert [info.name for info in response.models] == [
        name for name in BACKTEST_MODEL_NAMES if name in MODELS
    ]
    assert [info.kind for info in response.models] == [MODEL_KINDS[name] for name in MODELS]
    assert len(response.metrics) == len(report.scores)
    assert len(response.calibration) == len(report.calibration)

    scores = {
        (row.model, None if row.season_id is None else SEASON_CODES[row.season_id], row.subset): row
        for row in report.scores
    }
    for row in response.metrics:
        source = scores[(row.model, row.season_code, row.subset)]
        assert (row.n_matches, row.n_fixtures) == (source.n_matches, source.n_fixtures)
        assert row.coverage == source.n_matches / source.n_fixtures
        assert (row.log_loss, row.brier, row.rps, row.accuracy) == (
            source.log_loss,
            source.brier,
            source.rps,
            source.accuracy,
        )

    bins = {(row.model, row.outcome, row.bin_index): row for row in report.calibration}
    for bin_ in response.calibration:
        source_bin = bins[(bin_.model, bin_.outcome, bin_.bin_index)]
        assert (bin_.bin_lower, bin_.bin_upper, bin_.n) == (
            source_bin.bin_lower,
            source_bin.bin_upper,
            source_bin.n,
        )
        assert (bin_.mean_predicted, bin_.observed_frequency) == (
            source_bin.mean_predicted,
            source_bin.observed_frequency,
        )


def test_the_contract_order_follows_the_report_not_the_season_ids() -> None:
    data = make_synthetic_league()
    # All three seasons are scored, so the season rule is observable at all: the
    # default configuration scores only the last one.
    report = run_backtest(data, BacktestConfig(models=MODELS, from_year=2005))
    # Every list is reversed, so a converter that kept the report order or
    # sorted the season ids would fail below.
    reversed_report = replace(
        report,
        scores=list(reversed(report.scores)),
        calibration=list(reversed(report.calibration)),
    )

    response = _response(reversed_report, data)

    seasons: list[str] = []
    for row in reversed_report.scores:
        if row.season_id is None:
            continue
        code = SEASON_CODES[row.season_id]
        if code not in seasons:
            seasons.append(code)
    assert seasons == ["2007-08", "2006-07", "2005-06"]
    assert [(row.model, row.season_code, row.subset) for row in response.metrics] == [
        (model, season, subset)
        for model in MODELS
        for season in (*seasons, None)
        for subset in SUBSETS
    ]

    bin_indices = sorted({row.bin_index for row in report.calibration})
    assert [(bin_.model, bin_.outcome, bin_.bin_index) for bin_ in response.calibration] == [
        (model, outcome, bin_index)
        for model in MODELS
        for outcome in OUTCOMES
        for bin_index in bin_indices
    ]


def test_rounding_is_display_only_and_leaves_coverage_exact() -> None:
    data = make_synthetic_league()
    response = _response(run_backtest(data, BacktestConfig(models=MODELS)), data)

    rounded = round_response(response, digits=DISPLAY_DECIMALS)

    assert len(rounded.metrics) == len(response.metrics)
    for before, after in zip(response.metrics, rounded.metrics, strict=True):
        assert after.coverage == before.coverage
        assert after.coverage == after.n_matches / after.n_fixtures
        assert after.log_loss == round(before.log_loss, DISPLAY_DECIMALS)
        assert after.brier == round(before.brier, DISPLAY_DECIMALS)
        assert after.rps == round(before.rps, DISPLAY_DECIMALS)
        assert after.accuracy == round(before.accuracy, DISPLAY_DECIMALS)

    empty = [bin_ for bin_ in rounded.calibration if bin_.n == 0]
    assert empty, "the synthetic league must produce at least one empty calibration bin"
    for plain, displayed in zip(response.calibration, rounded.calibration, strict=True):
        assert displayed.bin_lower == round(plain.bin_lower, DISPLAY_DECIMALS)
        assert displayed.bin_upper == round(plain.bin_upper, DISPLAY_DECIMALS)
        assert displayed.mean_predicted == _rounded(plain.mean_predicted)
        assert displayed.observed_frequency == _rounded(plain.observed_frequency)
        assert (displayed.mean_predicted is None) == (plain.mean_predicted is None)
        assert (displayed.observed_frequency is None) == (plain.observed_frequency is None)


def test_the_rounded_response_round_trips_through_its_json() -> None:
    data = make_synthetic_league()
    response = _response(run_backtest(data, BacktestConfig(models=MODELS)), data)

    rounded = round_response(response)
    restored = ModelMetricsResponse.model_validate_json(rounded.model_dump_json())

    assert restored == rounded


def test_an_unknown_season_id_is_named_in_the_error() -> None:
    data = make_synthetic_league()
    # The default configuration scores only the third season.
    report = run_backtest(data, BacktestConfig(models=MODELS))

    with pytest.raises(ValueError, match="unknown season id 3"):
        _response(report, data, season_codes={1: SEASON_CODES[1], 2: SEASON_CODES[2]})


def _response(
    report: BacktestReport,
    data: MatchData,
    *,
    models: list[ModelInfo] | None = None,
    season_codes: dict[int, str] | None = None,
) -> ModelMetricsResponse:
    """Return the DTO of ``report``, with the synthetic season codes by default."""
    return report_to_response(
        report,
        run=_run(report, data),
        models=_models() if models is None else models,
        season_codes=SEASON_CODES if season_codes is None else season_codes,
    )


def _run(report: BacktestReport, data: MatchData) -> BacktestRunOut:
    """Return the header of an unsaved run: ``--format json --no-save`` output."""
    config = report.config
    return BacktestRunOut(
        id=None,
        started_at=None,
        finished_at=None,
        seed=config.seed,
        from_year=config.from_year,
        step_days=config.step_days,
        code_version="0.1.0",
        data_fingerprint=report.data_fingerprint,
        n_matches=len(data),
    )


def _models() -> list[ModelInfo]:
    """Return the scored models in report order, as ``describe()`` reports them."""
    return [
        ModelInfo(
            name=cast(ModelName, name),
            kind=MODEL_KINDS[name],
            version="1.0",
            hyperparameters={"seed": 7},
        )
        for name in BACKTEST_MODEL_NAMES
        if name in MODELS
    ]


def _rounded(value: float | None) -> float | None:
    """Return ``value`` rounded for display; ``None`` stays ``None``."""
    return None if value is None else round(value, DISPLAY_DECIMALS)
