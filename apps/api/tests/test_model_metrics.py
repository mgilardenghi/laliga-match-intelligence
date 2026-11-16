"""Contract tests for ``GET /v1/model-metrics`` against a disposable database.

Every test stores its own run through the real results store inside the test's
transaction, and asserts on that run only. The suite shares one database, so a
test never asserts on the absolute size of a table: a run is identified by the
id the store generated or, for the default, stamped far in the future so no row
another test left behind can be newer. A season code in the far future bounds
the metric rows to the season the test created.
"""

import datetime
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from lmi_api.main import create_app
from lmi_core.db.models import BacktestRun
from lmi_core.results.schemas import CalibrationBin, MetricRow, ModelInfo
from lmi_core.results.store import finish_run, start_run
from sqlalchemy import update
from sqlalchemy.orm import Session

from tests.factories import create_season

pytestmark = pytest.mark.integration

REPORT_REF = "#/components/schemas/ModelMetricsResponse"
PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type every error response is sent with."""

SEASON_CODE = "2098-99"
SEED = 7
FROM_YEAR = 2098
STEP_DAYS = 14
CODE_VERSION = "0.4.2+abc1234"
DATA_FINGERPRINT = "f" * 64
"""Run identity values the endpoint reads back from the stored row."""

EXPECTED_MODELS: list[dict[str, object]] = [
    {"name": "home_win", "kind": "baseline", "version": "1.0", "hyperparameters": {"clip": 0.001}},
    {"name": "base_rate", "kind": "baseline", "version": "1.0", "hyperparameters": {"prior": 0.45}},
    {
        "name": "market",
        "kind": "market",
        "version": "1.0",
        "hyperparameters": {"bookmaker": "pinnacle"},
    },
    {
        "name": "elo",
        "kind": "model",
        "version": "1.0",
        "hyperparameters": {"k": 20, "home_advantage": 60.0},
    },
    {
        "name": "dixon_coles",
        "kind": "model",
        "version": "1.0",
        "hyperparameters": {"xi": 0.0018},
    },
]
"""The five scored models in report order, which is not the order they are stored in."""


def metric(
    model: str,
    season_code: str | None,
    subset: str,
    *,
    n_matches: int,
    n_fixtures: int,
    log_loss: float,
    accuracy: float,
) -> dict[str, object]:
    """Return one metric row, with the two values the assertions pin.

    ``coverage`` and the two descriptors the test does not care about are
    derived or fixed here, so each row of the report list differs in the values
    a reader sees: the model, the season, the subset, the counts, the log loss
    and the accuracy.
    """
    return {
        "model": model,
        "season_code": season_code,
        "subset": subset,
        "n_matches": n_matches,
        "n_fixtures": n_fixtures,
        "coverage": n_matches / n_fixtures,
        "log_loss": log_loss,
        "brier": 0.5,
        "rps": 0.2,
        "accuracy": accuracy,
    }


def calibration_bin(
    model: str,
    outcome: str,
    bin_index: int,
    *,
    n: int,
    mean_predicted: float | None,
    observed_frequency: float | None,
) -> dict[str, object]:
    """Return one calibration bin; ``n = 0`` carries the two nulls."""
    return {
        "model": model,
        "outcome": outcome,
        "bin_index": bin_index,
        "bin_lower": bin_index / 10,
        "bin_upper": (bin_index + 1) / 10,
        "n": n,
        "mean_predicted": mean_predicted,
        "observed_frequency": observed_frequency,
    }


EXPECTED_METRICS: list[dict[str, object]] = [
    metric(
        "home_win",
        SEASON_CODE,
        "all",
        n_matches=250,
        n_fixtures=500,
        log_loss=1.02,
        accuracy=0.55,
    ),
    metric(
        "home_win",
        SEASON_CODE,
        "common",
        n_matches=200,
        n_fixtures=400,
        log_loss=1.03,
        accuracy=0.54,
    ),
    metric("home_win", None, "all", n_matches=250, n_fixtures=500, log_loss=1.04, accuracy=0.53),
    metric("home_win", None, "common", n_matches=200, n_fixtures=400, log_loss=1.05, accuracy=0.52),
    metric("elo", SEASON_CODE, "all", n_matches=250, n_fixtures=500, log_loss=0.92, accuracy=0.58),
    metric("elo", None, "all", n_matches=250, n_fixtures=500, log_loss=0.93, accuracy=0.57),
]
"""The report order: model, season, overall row last, ``all`` before ``common``."""

EXPECTED_CALIBRATION: list[dict[str, object]] = [
    calibration_bin("home_win", "H", 0, n=100, mean_predicted=0.05, observed_frequency=0.06),
    calibration_bin("home_win", "H", 1, n=0, mean_predicted=None, observed_frequency=None),
    calibration_bin("home_win", "D", 0, n=50, mean_predicted=0.25, observed_frequency=0.24),
    calibration_bin("elo", "H", 0, n=80, mean_predicted=0.06, observed_frequency=0.05),
]
"""The report order: model, outcome H/D/A, bin index; the second bin is empty."""


def store_run(
    session: Session,
    *,
    models: list[dict[str, object]],
    metrics: list[dict[str, object]],
    calibration: list[dict[str, object]],
    data_fingerprint: str = DATA_FINGERPRINT,
    n_matches: int = 250,
) -> uuid.UUID:
    """Store one succeeded run through the results store and return its id.

    The store commits on its own, and the test's session joins an outer
    transaction with savepoints, so the rows stay inside the test while the
    store keeps its production transaction boundaries.
    """
    run_id = start_run(
        session,
        seed=SEED,
        from_year=FROM_YEAR,
        step_days=STEP_DAYS,
        models=[ModelInfo.model_validate(item) for item in models],
        code_version=CODE_VERSION,
    )
    finish_run(
        session,
        run_id,
        metrics=[MetricRow.model_validate(item) for item in metrics],
        calibration=[CalibrationBin.model_validate(item) for item in calibration],
        data_fingerprint=data_fingerprint,
        n_matches=n_matches,
    )
    return run_id


def assert_no_backtest_run(response: httpx.Response) -> None:
    """Assert the response is the problem details body of a missing run."""
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "no backtest run",
        "errors": None,
    }


def test_get_model_metrics_returns_the_stored_run(
    api_client: TestClient, db_session: Session
) -> None:
    """The report is the run the store wrote, ordered by the store's contract.

    The models, the metric rows and the calibration bins are stored in reverse
    report order, so an endpoint that served the stored order instead of the
    contract order could not match the lists asserted here.
    """
    create_season(db_session, code=SEASON_CODE)
    run_id = store_run(
        db_session,
        models=list(reversed(EXPECTED_MODELS)),
        metrics=list(reversed(EXPECTED_METRICS)),
        calibration=list(reversed(EXPECTED_CALIBRATION)),
    )

    response = api_client.get("/v1/model-metrics", params={"run_id": str(run_id)})

    assert response.status_code == 200
    report = response.json()
    run = report["run"]
    assert run["id"] == str(run_id)
    assert run["seed"] == SEED
    assert run["from_year"] == FROM_YEAR
    assert run["step_days"] == STEP_DAYS
    assert run["code_version"] == CODE_VERSION
    assert run["data_fingerprint"] == DATA_FINGERPRINT
    assert run["n_matches"] == 250
    assert run["started_at"] is not None
    assert run["finished_at"] is not None
    assert report["models"] == EXPECTED_MODELS
    assert report["metrics"] == EXPECTED_METRICS
    assert report["calibration"] == EXPECTED_CALIBRATION


def test_get_model_metrics_without_a_run_id_returns_the_latest_succeeded_run(
    api_client: TestClient, db_session: Session
) -> None:
    """The default is the newest succeeded run, whatever else is stored.

    The two runs of the test are stamped in 2098, so a row the seed or another
    test left behind is never newer and the id of the second run is the only
    possible answer.
    """
    models = [EXPECTED_MODELS[0]]
    older = store_run(
        db_session,
        models=models,
        metrics=[EXPECTED_METRICS[2]],
        calibration=[],
        data_fingerprint="1" * 64,
    )
    newer = store_run(
        db_session,
        models=models,
        metrics=[EXPECTED_METRICS[3]],
        calibration=[],
        data_fingerprint="2" * 64,
    )
    db_session.execute(
        update(BacktestRun)
        .where(BacktestRun.id == older)
        .values(started_at=datetime.datetime(2098, 1, 1, tzinfo=datetime.UTC))
    )
    db_session.execute(
        update(BacktestRun)
        .where(BacktestRun.id == newer)
        .values(started_at=datetime.datetime(2098, 1, 2, tzinfo=datetime.UTC))
    )
    db_session.commit()

    response = api_client.get("/v1/model-metrics")

    assert response.status_code == 200
    report = response.json()
    assert report["run"]["id"] == str(newer)
    assert report["run"]["data_fingerprint"] == "2" * 64


def test_get_model_metrics_of_an_unknown_run_id_is_not_found(api_client: TestClient) -> None:
    response = api_client.get("/v1/model-metrics", params={"run_id": str(uuid.uuid4())})

    assert response.status_code == 404
    assert_no_backtest_run(response)


def test_get_model_metrics_of_a_running_run_is_not_found(
    api_client: TestClient, db_session: Session
) -> None:
    """A run that is still running has no results, so it is not served.

    The store returns ``None`` for it exactly as it does for a run that does not
    exist, which keeps the endpoint from disclosing which ids are in flight.
    """
    run_id = start_run(
        db_session,
        seed=SEED,
        from_year=FROM_YEAR,
        step_days=STEP_DAYS,
        models=[ModelInfo.model_validate(EXPECTED_MODELS[0])],
        code_version=CODE_VERSION,
    )

    response = api_client.get("/v1/model-metrics", params={"run_id": str(run_id)})

    assert response.status_code == 404
    assert_no_backtest_run(response)


def test_get_model_metrics_with_a_malformed_run_id_is_rejected_without_echoing_it(
    api_client: TestClient,
) -> None:
    response = api_client.get("/v1/model-metrics", params={"run_id": "not-a-uuid"})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["type"] == "about:blank"
    assert problem["title"] == "Unprocessable Content"
    assert problem["status"] == 422
    errors = problem["errors"]
    assert len(errors) == 1
    assert errors[0]["loc"] == ["query", "run_id"]
    assert isinstance(errors[0]["msg"], str) and errors[0]["msg"]
    assert isinstance(errors[0]["type"], str) and errors[0]["type"]
    assert "not-a-uuid" not in response.text


def test_get_model_metrics_serves_the_path_without_a_trailing_slash(
    api_client: TestClient,
) -> None:
    """The bare path is served directly instead of redirecting to the slash one.

    The test stores no run, and every test of this module runs inside a
    transaction that is rolled back -- a ``db_session.commit()`` only commits a
    savepoint -- so the shared database holds no committed backtest run. The
    default ``GET /v1/model-metrics`` therefore answers 404 deterministically,
    with the fixed problem body of a missing run, and the exact status and body
    are what this test pins.

    The assertion still proves the absence of a trailing-slash redirect: a route
    served at ``/v1/model-metrics/`` would answer 307 to this request, because
    ``follow_redirects`` is false, and not the 404 of the missing run.
    """
    response = api_client.get("/v1/model-metrics", follow_redirects=False)

    assert response.status_code == 404
    assert_no_backtest_run(response)


def test_openapi_documents_the_model_metrics_contract() -> None:
    document = create_app().openapi()
    operation = document["paths"]["/v1/model-metrics"]["get"]

    assert operation["operationId"] == "get_model_metrics"
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": REPORT_REF}

    for status in ("404", "422"):
        content = operation["responses"][status]["content"]
        assert PROBLEM_MEDIA_TYPE in content
        assert any(media.get("schema") == {"$ref": PROBLEM_REF} for media in content.values())

    assert "HTTPValidationError" not in document["components"]["schemas"]
