"""Contract tests for the model-metrics DTOs.

These tests need no database. They pin the JSON schema of
:class:`ModelMetricsResponse` as an inline snapshot, because the API, the store
and the worker CLI all exchange that exact shape, and they check the validation
rules the DTOs state themselves: the model-name literal, the fixture and match
counts, the metric ranges and the empty calibration bin.

The snapshot is a constant in this file on purpose. It is the contract, not a
copy of the implementation: a changed field name or range breaks this test and
the inline schema must be reviewed before it is regenerated.
"""

from typing import get_args

import pytest
from lmi_core.results.schemas import (
    BACKTEST_MODEL_NAMES,
    BacktestRunOut,
    CalibrationBin,
    MetricRow,
    ModelInfo,
    ModelMetricsResponse,
    ModelName,
)
from pydantic import ValidationError

MODEL_METRICS_RESPONSE_SCHEMA: dict[str, object] = {
    "$defs": {
        "BacktestRunOut": {
            "additionalProperties": False,
            "description": (
                "The header of one backtest run.\n\n"
                "``id``, ``started_at`` and ``finished_at`` are null only in the worker's\n"
                "``--format json --no-save`` output, where no run row exists. A stored run\n"
                "always carries all three."
            ),
            "properties": {
                "code_version": {"title": "Code Version", "type": "string"},
                "data_fingerprint": {"title": "Data Fingerprint", "type": "string"},
                "finished_at": {
                    "anyOf": [
                        {"format": "date-time", "type": "string"},
                        {"type": "null"},
                    ],
                    "default": None,
                    "title": "Finished At",
                },
                "from_year": {"title": "From Year", "type": "integer"},
                "id": {
                    "anyOf": [
                        {"format": "uuid", "type": "string"},
                        {"type": "null"},
                    ],
                    "default": None,
                    "title": "Id",
                },
                "n_matches": {"minimum": 0, "title": "N Matches", "type": "integer"},
                "seed": {"title": "Seed", "type": "integer"},
                "started_at": {
                    "anyOf": [
                        {"format": "date-time", "type": "string"},
                        {"type": "null"},
                    ],
                    "default": None,
                    "title": "Started At",
                },
                "step_days": {"minimum": 1, "title": "Step Days", "type": "integer"},
            },
            "required": [
                "seed",
                "from_year",
                "step_days",
                "code_version",
                "data_fingerprint",
                "n_matches",
            ],
            "title": "BacktestRunOut",
            "type": "object",
        },
        "CalibrationBin": {
            "additionalProperties": False,
            "description": (
                "One equal-width calibration bin of one model and outcome.\n\n"
                "An empty bin (``n = 0``) has no mean prediction and no observed frequency;\n"
                "the database stores that as two NULLs and this DTO states the same rule."
            ),
            "properties": {
                "bin_index": {
                    "maximum": 99,
                    "minimum": 0,
                    "title": "Bin Index",
                    "type": "integer",
                },
                "bin_lower": {"title": "Bin Lower", "type": "number"},
                "bin_upper": {"title": "Bin Upper", "type": "number"},
                "mean_predicted": {
                    "anyOf": [{"type": "number"}, {"type": "null"}],
                    "title": "Mean Predicted",
                },
                "model": {
                    "enum": [
                        "home_win",
                        "base_rate",
                        "market",
                        "elo",
                        "dixon_coles",
                    ],
                    "title": "Model",
                    "type": "string",
                },
                "n": {"minimum": 0, "title": "N", "type": "integer"},
                "observed_frequency": {
                    "anyOf": [{"type": "number"}, {"type": "null"}],
                    "title": "Observed Frequency",
                },
                "outcome": {
                    "enum": ["H", "D", "A"],
                    "title": "Outcome",
                    "type": "string",
                },
            },
            "required": [
                "model",
                "outcome",
                "bin_index",
                "bin_lower",
                "bin_upper",
                "n",
                "mean_predicted",
                "observed_frequency",
            ],
            "title": "CalibrationBin",
            "type": "object",
        },
        "MetricRow": {
            "additionalProperties": False,
            "description": (
                "The metrics of one model on one season or on all scored seasons.\n\n"
                "``season_code`` is ``seasons.code``; ``None`` is the overall row.\n"
                "``coverage`` is ``n_matches / n_fixtures``; ``load_report`` computes it, so\n"
                "a stored row does not carry it."
            ),
            "properties": {
                "accuracy": {
                    "maximum": 1,
                    "minimum": 0,
                    "title": "Accuracy",
                    "type": "number",
                },
                "brier": {
                    "maximum": 2,
                    "minimum": 0,
                    "title": "Brier",
                    "type": "number",
                },
                "coverage": {
                    "maximum": 1,
                    "minimum": 0,
                    "title": "Coverage",
                    "type": "number",
                },
                "log_loss": {"minimum": 0, "title": "Log Loss", "type": "number"},
                "model": {
                    "enum": [
                        "home_win",
                        "base_rate",
                        "market",
                        "elo",
                        "dixon_coles",
                    ],
                    "title": "Model",
                    "type": "string",
                },
                "n_fixtures": {"minimum": 0, "title": "N Fixtures", "type": "integer"},
                "n_matches": {
                    "exclusiveMinimum": 0,
                    "title": "N Matches",
                    "type": "integer",
                },
                "rps": {
                    "maximum": 1,
                    "minimum": 0,
                    "title": "Rps",
                    "type": "number",
                },
                "season_code": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "title": "Season Code",
                },
                "subset": {
                    "enum": ["all", "common"],
                    "title": "Subset",
                    "type": "string",
                },
            },
            "required": [
                "model",
                "season_code",
                "subset",
                "n_matches",
                "n_fixtures",
                "coverage",
                "log_loss",
                "brier",
                "rps",
                "accuracy",
            ],
            "title": "MetricRow",
            "type": "object",
        },
        "ModelInfo": {
            "additionalProperties": False,
            "description": (
                "One model of a run, as ``describe()`` reports it.\n\n"
                "The hyperparameters are recorded next to the metrics so a reader can tell\n"
                "which settings produced them. ``version`` is bumped whenever a\n"
                "hyperparameter or the math changes."
            ),
            "properties": {
                "hyperparameters": {
                    "additionalProperties": {
                        "anyOf": [
                            {"type": "number"},
                            {"type": "integer"},
                            {"type": "string"},
                        ]
                    },
                    "title": "Hyperparameters",
                    "type": "object",
                },
                "kind": {
                    "enum": ["baseline", "market", "model"],
                    "title": "Kind",
                    "type": "string",
                },
                "name": {
                    "enum": [
                        "home_win",
                        "base_rate",
                        "market",
                        "elo",
                        "dixon_coles",
                    ],
                    "title": "Name",
                    "type": "string",
                },
                "version": {"title": "Version", "type": "string"},
            },
            "required": ["name", "kind", "version", "hyperparameters"],
            "title": "ModelInfo",
            "type": "object",
        },
    },
    "additionalProperties": False,
    "description": (
        "One backtest run and its results, ordered per the API contract.\n\n"
        "The lists are ordered by the producer: models in :data:`BACKTEST_MODEL_NAMES`\n"
        "order, metrics by season and then ``all`` before ``common``, and\n"
        "calibration by model, outcome and bin index."
    ),
    "properties": {
        "calibration": {
            "items": {"$ref": "#/$defs/CalibrationBin"},
            "title": "Calibration",
            "type": "array",
        },
        "metrics": {
            "items": {"$ref": "#/$defs/MetricRow"},
            "title": "Metrics",
            "type": "array",
        },
        "models": {
            "items": {"$ref": "#/$defs/ModelInfo"},
            "title": "Models",
            "type": "array",
        },
        "run": {"$ref": "#/$defs/BacktestRunOut"},
    },
    "required": ["run", "models", "metrics", "calibration"],
    "title": "ModelMetricsResponse",
    "type": "object",
}


def model_info(name: ModelName = "home_win") -> ModelInfo:
    """Return one valid model description."""
    return ModelInfo(
        name=name,
        kind="baseline" if name in {"home_win", "base_rate"} else "model",
        version="1.0",
        hyperparameters={"clip": 1e-15, "home_advantage": 60, "variant": "base"},
    )


def metric_row(**overrides: object) -> MetricRow:
    """Return one valid metric row, with ``overrides`` applied."""
    values: dict[str, object] = {
        "model": "home_win",
        "season_code": "2024-25",
        "subset": "all",
        "n_matches": 300,
        "n_fixtures": 380,
        "coverage": 300 / 380,
        "log_loss": 0.9,
        "brier": 0.55,
        "rps": 0.2,
        "accuracy": 0.5,
    }
    values.update(overrides)
    return MetricRow.model_validate(values)


def calibration_bin(**overrides: object) -> CalibrationBin:
    """Return one valid calibration bin, with ``overrides`` applied."""
    values: dict[str, object] = {
        "model": "elo",
        "outcome": "H",
        "bin_index": 0,
        "bin_lower": 0.0,
        "bin_upper": 0.1,
        "n": 100,
        "mean_predicted": 0.05,
        "observed_frequency": 0.04,
    }
    values.update(overrides)
    return CalibrationBin.model_validate(values)


def test_json_schema_snapshot_is_the_contract() -> None:
    assert ModelMetricsResponse.model_json_schema() == MODEL_METRICS_RESPONSE_SCHEMA


def test_backtest_model_names_match_the_literal_in_report_order() -> None:
    assert BACKTEST_MODEL_NAMES == ("home_win", "base_rate", "market", "elo", "dixon_coles")
    assert set(get_args(ModelName)) == set(BACKTEST_MODEL_NAMES)


def test_unknown_model_name_is_rejected() -> None:
    with pytest.raises(ValidationError, match="home_win"):
        ModelInfo.model_validate(
            {"name": "gradient_boost", "kind": "model", "version": "1", "hyperparameters": {}}
        )
    with pytest.raises(ValidationError, match="home_win"):
        metric_row(model="gradient_boost")
    with pytest.raises(ValidationError, match="home_win"):
        calibration_bin(model="gradient_boost")


def test_metric_row_rejects_more_matches_than_fixtures() -> None:
    with pytest.raises(ValidationError, match="n_fixtures"):
        metric_row(n_matches=380, n_fixtures=300)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"n_matches": 0}, id="zero-matches"),
        pytest.param({"log_loss": -0.1}, id="negative-log-loss"),
        pytest.param({"brier": 2.5}, id="brier-above-two"),
        pytest.param({"rps": 1.5}, id="rps-above-one"),
        pytest.param({"accuracy": -0.1}, id="negative-accuracy"),
        pytest.param({"coverage": 1.5}, id="coverage-above-one"),
    ],
)
def test_metric_row_rejects_out_of_range_values(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        metric_row(**overrides)


def test_metric_row_accepts_the_range_boundaries() -> None:
    row = metric_row(brier=0.0, rps=0.0, accuracy=0.0, log_loss=0.0, coverage=0.0)
    assert row.log_loss == 0.0
    assert metric_row(brier=2.0, rps=1.0, accuracy=1.0, coverage=1.0).brier == 2.0


def test_calibration_bin_rejects_a_summary_on_an_empty_bin() -> None:
    with pytest.raises(ValidationError, match="mean_predicted"):
        calibration_bin(n=0, mean_predicted=0.5, observed_frequency=0.5)
    with pytest.raises(ValidationError, match="observed_frequency"):
        calibration_bin(n=0, mean_predicted=None, observed_frequency=0.4)


def test_calibration_bin_accepts_an_empty_bin() -> None:
    row = calibration_bin(n=0, mean_predicted=None, observed_frequency=None)
    assert row.n == 0


def test_response_accepts_a_well_formed_report() -> None:
    response = ModelMetricsResponse(
        run=BacktestRunOut(
            seed=42,
            from_year=2007,
            step_days=7,
            code_version="0.1.0",
            data_fingerprint="a" * 64,
            n_matches=380,
        ),
        models=[model_info()],
        metrics=[metric_row()],
        calibration=[calibration_bin()],
    )
    assert response.run.id is None
    assert response.models[0].name == "home_win"
