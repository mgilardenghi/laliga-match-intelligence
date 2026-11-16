"""Contract tests for the model-metrics DTOs.

These tests need no database. They pin the JSON schema of
:class:`ModelMetricsResponse` as an inline snapshot, because the API, the store
and the worker CLI all exchange that exact shape, and they check the validation
rules the DTOs state themselves: the model-name literal, the fixture and match
counts, the coverage ratio, the metric and calibration ranges, the non-finite
floats, the run identity and the empty calibration bin.

The snapshot is a constant in this file on purpose. It is the contract, not a
copy of the implementation: a changed field name, type, optionality, default or
numeric bound breaks this test and the inline schema must be reviewed before it
is regenerated. Every ``description`` is removed from both sides before the
comparison, because Pydantic copies each DTO docstring into the schema and a
wording edit must not fail a shape contract.
"""

import copy
import math
from datetime import UTC, datetime, timedelta
from typing import cast, get_args
from uuid import uuid4

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
            "properties": {
                "bin_index": {
                    "maximum": 99,
                    "minimum": 0,
                    "title": "Bin Index",
                    "type": "integer",
                },
                "bin_lower": {
                    "maximum": 1,
                    "minimum": 0,
                    "title": "Bin Lower",
                    "type": "number",
                },
                "bin_upper": {
                    "maximum": 1,
                    "minimum": 0,
                    "title": "Bin Upper",
                    "type": "number",
                },
                "mean_predicted": {
                    "anyOf": [
                        {"maximum": 1, "minimum": 0, "type": "number"},
                        {"type": "null"},
                    ],
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
                    "anyOf": [
                        {"maximum": 1, "minimum": 0, "type": "number"},
                        {"type": "null"},
                    ],
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


def run_out(**overrides: object) -> BacktestRunOut:
    """Return one valid run header of a ``--no-save`` run, with ``overrides`` applied."""
    values: dict[str, object] = {
        "seed": 42,
        "from_year": 2007,
        "step_days": 7,
        "code_version": "0.1.0",
        "data_fingerprint": "a" * 64,
        "n_matches": 380,
    }
    values.update(overrides)
    return BacktestRunOut.model_validate(values)


def _without_descriptions(value: object) -> object:
    """Return ``value`` with every ``description`` key removed, at any depth."""
    if isinstance(value, dict):
        return {
            key: _without_descriptions(item)
            for key, item in cast(dict[str, object], value).items()
            if key != "description"
        }
    if isinstance(value, list):
        return [_without_descriptions(item) for item in cast(list[object], value)]
    return value


def without_descriptions(schema: dict[str, object]) -> dict[str, object]:
    """Return a JSON schema with every ``description`` key removed.

    Pydantic copies a model docstring into the schema as a ``description``. The
    docstrings of these DTOs are explanatory prose, so pinning them would fail
    the shape contract on a wording edit alone. Only that key is dropped: field
    names, types, ``required`` entries, defaults and numeric bounds are still
    compared, so the snapshot still fails on a shape change.
    """
    return cast(dict[str, object], _without_descriptions(schema))


def schema_properties(schema: dict[str, object], name: str) -> dict[str, object]:
    """Return the ``properties`` of the ``$defs`` entry ``name`` in ``schema``."""
    definitions = cast(dict[str, dict[str, object]], schema["$defs"])
    return cast(dict[str, object], definitions[name]["properties"])


def test_json_schema_snapshot_is_the_contract() -> None:
    assert without_descriptions(ModelMetricsResponse.model_json_schema()) == (
        MODEL_METRICS_RESPONSE_SCHEMA
    )


def test_schema_comparison_still_detects_a_shape_change() -> None:
    """Dropping descriptions must not turn the snapshot into a tautology."""
    generated = without_descriptions(ModelMetricsResponse.model_json_schema())

    renamed = copy.deepcopy(MODEL_METRICS_RESPONSE_SCHEMA)
    properties = schema_properties(renamed, "MetricRow")
    properties["n_fixtures_renamed"] = properties.pop("n_fixtures")
    assert generated != renamed

    redefaulted = copy.deepcopy(MODEL_METRICS_RESPONSE_SCHEMA)
    schema_properties(redefaulted, "BacktestRunOut")["seed"] = {
        "default": 42,
        "title": "Seed",
        "type": "integer",
    }
    assert generated != redefaulted

    rebounded = copy.deepcopy(MODEL_METRICS_RESPONSE_SCHEMA)
    n_matches = cast(dict[str, object], schema_properties(rebounded, "MetricRow")["n_matches"])
    n_matches["exclusiveMinimum"] = -1
    assert generated != rebounded


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


def test_metric_row_rejects_coverage_that_contradicts_the_counts() -> None:
    accepted = metric_row(n_matches=300, n_fixtures=380, coverage=300 / 380)
    assert accepted.coverage == 300 / 380

    with pytest.raises(ValidationError, match="coverage"):
        metric_row(coverage=0.5)
    with pytest.raises(ValidationError, match="coverage"):
        metric_row(coverage=300 / 380 + 0.01)


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
    """Each metric sits on its own bounds, with coverage derived from the counts.

    ``coverage`` is ``n_matches / n_fixtures`` and ``n_matches`` is positive, so
    its lower end is one match out of the fixture count, not zero.
    """
    lowest = metric_row(
        n_matches=1,
        n_fixtures=380,
        coverage=1 / 380,
        brier=0.0,
        rps=0.0,
        accuracy=0.0,
        log_loss=0.0,
    )
    assert lowest.log_loss == 0.0
    assert lowest.brier == 0.0
    assert lowest.rps == 0.0
    assert lowest.accuracy == 0.0
    assert lowest.coverage == 1 / 380

    highest = metric_row(
        n_matches=380,
        n_fixtures=380,
        coverage=1.0,
        brier=2.0,
        rps=1.0,
        accuracy=1.0,
    )
    assert highest.brier == 2.0
    assert highest.rps == 1.0
    assert highest.accuracy == 1.0
    assert highest.coverage == 1.0


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"coverage": math.nan}, id="nan-coverage"),
        pytest.param({"coverage": math.inf}, id="inf-coverage"),
        pytest.param({"log_loss": math.nan}, id="nan-log-loss"),
        pytest.param({"log_loss": math.inf}, id="inf-log-loss"),
        pytest.param({"brier": math.nan}, id="nan-brier"),
        pytest.param({"brier": math.inf}, id="inf-brier"),
        pytest.param({"rps": math.nan}, id="nan-rps"),
        pytest.param({"rps": math.inf}, id="inf-rps"),
        pytest.param({"accuracy": math.nan}, id="nan-accuracy"),
        pytest.param({"accuracy": math.inf}, id="inf-accuracy"),
    ],
)
def test_metric_row_rejects_a_non_finite_metric(overrides: dict[str, object]) -> None:
    """No metric may be NaN or infinite, including ``log_loss``, which has no ceiling."""
    with pytest.raises(ValidationError, match="finite number"):
        metric_row(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"bin_lower": math.nan}, id="nan-bin-lower"),
        pytest.param({"bin_lower": math.inf}, id="inf-bin-lower"),
        pytest.param({"bin_upper": math.nan}, id="nan-bin-upper"),
        pytest.param({"bin_upper": math.inf}, id="inf-bin-upper"),
        pytest.param({"mean_predicted": math.nan}, id="nan-mean-predicted"),
        pytest.param({"mean_predicted": math.inf}, id="inf-mean-predicted"),
        pytest.param({"observed_frequency": math.nan}, id="nan-observed-frequency"),
        pytest.param({"observed_frequency": math.inf}, id="inf-observed-frequency"),
    ],
)
def test_calibration_bin_rejects_a_non_finite_value(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="finite number"):
        calibration_bin(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"mean_predicted": -0.01}, id="mean-predicted-below-zero"),
        pytest.param({"mean_predicted": 1.01}, id="mean-predicted-above-one"),
        pytest.param({"observed_frequency": -0.01}, id="observed-frequency-below-zero"),
        pytest.param({"observed_frequency": 1.01}, id="observed-frequency-above-one"),
        pytest.param({"bin_lower": -0.01}, id="bin-lower-below-zero"),
        pytest.param(
            {"bin_lower": -0.01, "bin_upper": -0.01},
            id="bin-upper-below-zero",
        ),
        pytest.param(
            {"bin_lower": 1.01, "bin_upper": 1.01},
            id="bin-lower-above-one",
        ),
        pytest.param({"bin_upper": 1.01}, id="bin-upper-above-one"),
    ],
)
def test_calibration_bin_rejects_out_of_range_values(overrides: dict[str, object]) -> None:
    """Every calibration value is a probability or a probability bin edge.

    ``bin_lower`` cannot leave ``0..1`` while staying below ``bin_upper``, so the
    cases that put an edge out of range move both edges together. That way the
    ordering rule of :func:`test_calibration_bin_rejects_a_lower_edge_above_the_upper_edge`
    is not the reason a case fails.
    """
    with pytest.raises(ValidationError):
        calibration_bin(**overrides)


def test_calibration_bin_accepts_the_range_boundaries() -> None:
    """The first bin starts at zero, the last one ends at one, and both ends are valid."""
    first = calibration_bin(bin_lower=0.0, mean_predicted=0.0, observed_frequency=0.0)
    assert (first.bin_lower, first.mean_predicted, first.observed_frequency) == (0.0, 0.0, 0.0)

    last = calibration_bin(bin_lower=0.9, bin_upper=1.0, mean_predicted=1.0, observed_frequency=1.0)
    assert (last.bin_upper, last.mean_predicted, last.observed_frequency) == (1.0, 1.0, 1.0)


def test_calibration_bin_rejects_a_lower_edge_above_the_upper_edge() -> None:
    with pytest.raises(ValidationError, match="bin_lower"):
        calibration_bin(bin_lower=0.5, bin_upper=0.4)


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


def test_backtest_run_out_accepts_the_two_real_identity_states() -> None:
    no_save = run_out()
    assert (no_save.id, no_save.started_at, no_save.finished_at) == (None, None, None)

    started_at = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    stored = run_out(
        id=uuid4(),
        started_at=started_at,
        finished_at=started_at + timedelta(minutes=4),
    )
    assert stored.id is not None
    assert stored.started_at == started_at
    assert stored.finished_at is not None
    assert stored.finished_at > stored.started_at


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"id": uuid4()}, id="only-id"),
        pytest.param(
            {"started_at": datetime(2026, 11, 15, 12, 0, tzinfo=UTC)}, id="only-started-at"
        ),
        pytest.param(
            {"finished_at": datetime(2026, 11, 15, 12, 4, tzinfo=UTC)}, id="only-finished-at"
        ),
        pytest.param(
            {
                "id": uuid4(),
                "started_at": datetime(2026, 11, 15, 12, 0, tzinfo=UTC),
            },
            id="missing-finished-at",
        ),
        pytest.param(
            {
                "id": uuid4(),
                "finished_at": datetime(2026, 11, 15, 12, 4, tzinfo=UTC),
            },
            id="missing-started-at",
        ),
        pytest.param(
            {
                "started_at": datetime(2026, 11, 15, 12, 0, tzinfo=UTC),
                "finished_at": datetime(2026, 11, 15, 12, 4, tzinfo=UTC),
            },
            id="missing-id",
        ),
    ],
)
def test_backtest_run_out_rejects_a_partial_identity(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="set together"):
        run_out(**overrides)
