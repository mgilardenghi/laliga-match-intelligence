"""Contract tests for ``GET /v1/model-metrics/live`` on a disposable database.

The suite shares one database with any ingested data, so every test bounds its
request with the synthetic season code it created and asserts on the rows it
stored itself, never on the absolute size of a table.

A stored prediction cannot be inserted freely: the ``predictions_guard_insert``
trigger accepts only a scheduled match dated after Madrid today and stamps
``created_at`` itself. Every fixture here therefore derives its dates from
:func:`tests.live_support.madrid_today`, which reads the Madrid date of the
database, and the season codes are far in the future so no seeded season can
collide with them. A match becomes ``played`` or ``withdrawn`` only after its
predictions are stored.

The expectations are the stored rows these helpers created and the scores the
core scorer derives from them. Nothing is recomputed here with the code under
test.
"""

import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from lmi_api.main import create_app
from lmi_core.db.models import Match, Season
from sqlalchemy.orm import Session

from tests.factories import create_match, create_season, create_team
from tests.live_metrics_support import store_odds
from tests.live_support import (
    create_prediction,
    madrid_today,
    mark_played,
    mark_withdrawn,
    move_match,
)

pytestmark = pytest.mark.integration

SEASON_CODE = "2098-99"
"""The synthetic season code of these tests: no data source covers 2098."""

SEASON_START_YEAR = 2098
"""The start year of :data:`SEASON_CODE`, which the season table keeps unique."""

LATEST_SEASON_CODE = "2099-00"
LATEST_SEASON_START_YEAR = 2099
"""A second season starting later, so the default selection has a definite answer."""

LIVE_METRICS_REF = "#/components/schemas/LiveMetricsResponse"
PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type every error response is sent with."""

SCORED_MODELS = ("home_win", "dixon_coles")
"""Two model names in report order, so a stored-order answer could not match."""

COUNT_FIELDS = (
    "n_rows",
    "n_scored",
    "n_pending",
    "n_superseded",
    "n_late",
    "n_withdrawn",
)
"""Every counter of the report's ``counts`` object."""

SCORE_FIELDS = ("log_loss", "brier", "rps", "accuracy")
"""Every aggregate score of one model."""


def test_a_season_without_stored_predictions_is_an_empty_report(
    api_client: TestClient, db_session: Session
) -> None:
    """No prediction in the season means no model, no comparison and no counts."""
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)

    payload = _live(api_client, season=season.code)

    assert set(payload) == {
        "evaluated_as_of",
        "models",
        "common",
        "market_reference",
        "counts",
    }
    assert payload["evaluated_as_of"] == today.isoformat()
    assert payload["models"] == []
    assert payload["common"] is None
    assert payload["market_reference"] is None
    assert set(payload["counts"]) == set(COUNT_FIELDS)
    assert all(payload["counts"][field] == 0 for field in COUNT_FIELDS)


def test_pending_rows_are_counted_and_never_scored(
    api_client: TestClient, db_session: Session
) -> None:
    """A scheduled match has forecasts but no result, so it contributes no score."""
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    match = _fixture(db_session, season, kickoff_date=today + _days(3), tag="pending")
    for model in SCORED_MODELS:
        create_prediction(db_session, match=match, model=model)

    payload = _live(api_client, season=season.code)

    assert payload["counts"]["n_rows"] == 2
    assert payload["counts"]["n_pending"] == 2
    assert payload["counts"]["n_scored"] == 0
    assert payload["models"] == []
    assert payload["common"] is None
    assert payload["market_reference"] is None


def test_scored_rows_are_aggregated_per_model_and_version_in_report_order(
    api_client: TestClient, db_session: Session
) -> None:
    """One entry per ``(model, model_version)``, whatever order the rows were stored in."""
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    match = _fixture(db_session, season, kickoff_date=today + _days(4), tag="scored")
    create_prediction(db_session, match=match, model="dixon_coles", model_version="2.0")
    create_prediction(db_session, match=match, model="home_win")
    mark_played(db_session, match, home_goals=1, away_goals=0)

    payload = _live(api_client, season=season.code)

    assert payload["counts"]["n_rows"] == 2
    assert payload["counts"]["n_scored"] == 2
    models = payload["models"]
    assert [entry["model"] for entry in models] == list(SCORED_MODELS)
    assert [entry["model_version"] for entry in models] == ["1.0", "2.0"]
    for entry in models:
        assert set(entry) == {"model", "model_version", "scores"}
        scores = entry["scores"]
        assert scores["n_matches"] == 1
        assert set(scores) == {"n_matches", *SCORE_FIELDS}
        for field in SCORE_FIELDS:
            assert isinstance(scores[field], float)
            assert scores[field] >= 0.0

    common = payload["common"]
    assert common is not None
    assert common["match_count"] == 1
    assert [entry["model"] for entry in common["models"]] == list(SCORED_MODELS)
    assert payload["market_reference"] is None


def test_the_market_reference_is_recomputed_from_the_pre_closing_price(
    api_client: TestClient, db_session: Session
) -> None:
    """A stored price gives a market benchmark on the shared scored cohort."""
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    match = _scored_match(db_session, season, kickoff_date=today + _days(5), tag="market-price")
    store_odds(db_session, match=match)

    payload = _live(api_client, season=season.code)

    reference = payload["market_reference"]
    assert reference is not None
    assert reference["model"] == "market"
    assert reference["stored_before_kickoff"] is False
    assert reference["match_count"] == payload["common"]["match_count"]
    assert reference["match_count"] == 1
    assert reference["matches_without_odds"] == 0
    scores = reference["scores"]
    assert scores["n_matches"] == 1
    for field in SCORE_FIELDS:
        assert isinstance(scores[field], float)
        assert scores[field] >= 0.0


def test_without_a_price_the_market_reference_is_absent(
    api_client: TestClient, db_session: Session
) -> None:
    """No odds on the only shared match means no benchmark, not a zero score."""
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    _scored_match(db_session, season, kickoff_date=today + _days(6), tag="market-missing")

    payload = _live(api_client, season=season.code)

    assert payload["common"]["match_count"] == 1
    assert payload["market_reference"] is None


def test_a_shared_match_without_a_price_is_counted_in_matches_without_odds(
    api_client: TestClient, db_session: Session
) -> None:
    """The benchmark keeps the priced matches and reports the ones it skipped."""
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    priced = _scored_match(db_session, season, kickoff_date=today + _days(7), tag="priced")
    _scored_match(db_session, season, kickoff_date=today + _days(8), tag="unpriced")
    store_odds(db_session, match=priced)

    payload = _live(api_client, season=season.code)

    assert payload["models"][0]["scores"]["n_matches"] == 2
    assert payload["common"]["match_count"] == 2
    reference = payload["market_reference"]
    assert reference is not None
    assert reference["match_count"] == 1
    assert reference["matches_without_odds"] == 1
    assert reference["scores"]["n_matches"] == 1


def test_withdrawn_and_late_rows_are_counted_and_never_scored(
    api_client: TestClient, db_session: Session
) -> None:
    """A withdrawn fixture and a fixture moved earlier leave their rows unsettled."""
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    withdrawn_match = _fixture(db_session, season, kickoff_date=today + _days(9), tag="withdrawn")
    create_prediction(db_session, match=withdrawn_match)
    mark_withdrawn(db_session, withdrawn_match)

    late_match = _fixture(db_session, season, kickoff_date=today + _days(10), tag="late")
    create_prediction(db_session, match=late_match)
    mark_played(db_session, late_match, home_goals=0, away_goals=2)
    move_match(db_session, late_match, kickoff_date=today - _days(1))

    payload = _live(api_client, season=season.code)

    assert payload["counts"]["n_rows"] == 2
    assert payload["counts"]["n_withdrawn"] == 1
    assert payload["counts"]["n_late"] == 1
    assert payload["counts"]["n_scored"] == 0
    assert payload["models"] == []
    assert payload["common"] is None
    assert payload["market_reference"] is None


def test_the_season_filter_bounds_the_report_and_the_default_serves_the_latest(
    api_client: TestClient, db_session: Session
) -> None:
    """One request per season scores only that season; no season takes the newest."""
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    latest = create_season(db_session, code=LATEST_SEASON_CODE, start_year=LATEST_SEASON_START_YEAR)
    _scored_match(db_session, season, kickoff_date=today + _days(3), tag="old")
    _scored_match(
        db_session,
        latest,
        kickoff_date=today + _days(4),
        tag="new",
        models=("home_win",),
    )

    explicit_old = _live(api_client, season=season.code)
    explicit_new = _live(api_client, season=latest.code)
    default = _live(api_client)

    assert [entry["model"] for entry in explicit_old["models"]] == list(SCORED_MODELS)
    assert explicit_old["counts"]["n_scored"] == 2
    assert [entry["model"] for entry in explicit_new["models"]] == ["home_win"]
    assert explicit_new["counts"]["n_scored"] == 1
    assert default["models"] == explicit_new["models"]
    assert default["counts"] == explicit_new["counts"]


def test_an_unknown_season_is_not_found_without_echoing_it(api_client: TestClient) -> None:
    response = api_client.get("/v1/model-metrics/live", params={"season": "1999-00"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Not Found"
    assert problem["status"] == 404
    assert problem["detail"] == "Season not found"
    assert problem["errors"] is None
    assert "1999-00" not in response.text


def test_openapi_documents_the_live_metrics_contract() -> None:
    document = create_app().openapi()
    operation = document["paths"]["/v1/model-metrics/live"]["get"]

    assert operation["operationId"] == "get_live_model_metrics"
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": LIVE_METRICS_REF}

    for status in ("404", "422"):
        content = operation["responses"][status]["content"]
        assert PROBLEM_MEDIA_TYPE in content
        assert any(media.get("schema") == {"$ref": PROBLEM_REF} for media in content.values())

    assert document["paths"]["/v1/model-metrics"]["get"]["operationId"] == "get_model_metrics"
    assert "HTTPValidationError" not in document["components"]["schemas"]


def _live(api_client: TestClient, **params: str) -> dict[str, Any]:
    """Return the successful live-metrics payload of one request."""
    response = api_client.get("/v1/model-metrics/live", params=params)
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _days(count: int) -> datetime.timedelta:
    """Return ``count`` whole days as a timedelta."""
    return datetime.timedelta(days=count)


def _fixture(session: Session, season: Season, *, kickoff_date: datetime.date, tag: str) -> Match:
    """Insert one scheduled fixture of ``season`` with a pair of synthetic teams."""
    home = create_team(session, name=f"Synthetic Home {tag}")
    away = create_team(session, name=f"Synthetic Away {tag}")
    return create_match(session, season=season, home=home, away=away, kickoff_date=kickoff_date)


def _scored_match(
    session: Session,
    season: Season,
    *,
    kickoff_date: datetime.date,
    tag: str,
    models: tuple[str, ...] = SCORED_MODELS,
) -> Match:
    """Store one prediction per model on a fixture, then move it to played.

    The predictions come first on purpose: the insert guard refuses a match that
    is no longer scheduled, so the fixture can only become played after its rows
    are stored, which is exactly the order the worker uses.
    """
    match = _fixture(session, season, kickoff_date=kickoff_date, tag=tag)
    for model in models:
        create_prediction(session, match=match, model=model)
    mark_played(session, match, home_goals=2, away_goals=0)
    return match
