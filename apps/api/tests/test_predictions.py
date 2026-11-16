"""Contract tests for the ``/v1/predictions`` endpoints on a disposable database.

The suite shares its database with the seasons and the matches the real
ingestion left behind, so every test bounds its requests with the synthetic
season code it created and asserts on the rows it stored itself, never on the
absolute size of the predictions table.

Predictions cannot be inserted freely: the ``predictions_guard_insert`` trigger
accepts only a scheduled match dated after Madrid today and stamps ``created_at``
itself. Every fixture here therefore derives its dates from
:func:`tests.live_support.madrid_today`, which reads the Madrid date of the
database, and the season code is far in the future so no seeded or ingested
season can collide with it.

The expectations are the rows these helpers created. Nothing is recomputed from
the code under test.
"""

import datetime
from typing import Any, NamedTuple

import pytest
from fastapi.testclient import TestClient
from lmi_api import views
from lmi_api.main import create_app
from lmi_core.db.models import Match, Season
from lmi_core.live.scoring import evaluate
from sqlalchemy.orm import Session

from tests.factories import create_match, create_season, create_team
from tests.live_support import (
    create_ledger_prediction,
    create_prediction,
    create_prediction_run,
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

PREDICTION_PAGE_REF = "#/components/schemas/PredictionPage"
PREDICTION_OUT_REF = "#/components/schemas/PredictionOut"
PREDICTION_DETAIL_REF = "#/components/schemas/PredictionDetail"
PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type every error response is sent with."""

PREDICTION_FIELDS = {
    "id",
    "match",
    "model",
    "model_version",
    "as_of_date",
    "created_at",
    "probabilities",
    "odds_source",
    "evaluation",
    "score",
}
"""Every field a collection row carries, whatever the evaluation."""

DETAIL_FIELDS = PREDICTION_FIELDS | {"run_id", "model_config"}
"""Every field a detail row adds to a collection row."""


class Ledger(NamedTuple):
    """The stored identifiers of one world that holds every evaluation once."""

    season: Season
    pending: int
    scored: int
    superseded: int
    late: int
    withdrawn: int


def test_the_default_listing_serves_one_standing_row_per_match_and_model(
    api_client: TestClient, db_session: Session
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    run = create_prediction_run(db_session)
    first = _fixture(db_session, season, kickoff_date=today + _days(3), tag="first")
    second = _fixture(db_session, season, kickoff_date=today + _days(5), tag="second")
    home_win = create_prediction(db_session, match=first, model="home_win", run=run)
    dixon_coles = create_prediction(db_session, match=first, model="dixon_coles", run=run)
    elsewhere = create_prediction(db_session, match=second, model="home_win", run=run)
    replaced = create_ledger_prediction(
        db_session, match=first, model="home_win", as_of=today - _days(1), run=run
    )

    response = api_client.get("/v1/predictions", params={"season": season.code})

    assert response.status_code == 200
    page = response.json()
    served = [item["id"] for item in page["items"]]
    assert served == [home_win.id, dixon_coles.id, elsewhere.id]
    assert replaced.id not in served
    assert page["total"] == 3
    assert page["limit"] == 100
    assert page["offset"] == 0
    for item in page["items"]:
        assert set(item) == PREDICTION_FIELDS
        assert item["evaluation"] == "pending"
        assert item["score"] is None
        assert item["model_version"] == "1.0"
        assert item["as_of_date"] == today.isoformat()
        assert item["odds_source"] is None
        assert item["match"]["id"] == first.id or item["match"]["id"] == second.id
    assert page["items"][0]["match"]["id"] == first.id
    assert page["items"][0]["probabilities"] == {"home": 0.5, "draw": 0.3, "away": 0.2}
    assert page["items"][0]["model"] == "home_win"
    assert _as_datetime(page["items"][0]["created_at"]).tzinfo is not None


def test_latest_only_false_serves_the_whole_ledger_with_the_older_row_superseded(
    api_client: TestClient, db_session: Session
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    run = create_prediction_run(db_session)
    match = _fixture(db_session, season, kickoff_date=today + _days(4), tag="ledger")
    older = create_ledger_prediction(
        db_session, match=match, model="home_win", as_of=today - _days(2), run=run
    )
    newest = create_prediction(db_session, match=match, model="home_win", run=run)

    response = api_client.get(
        "/v1/predictions", params={"season": season.code, "latest_only": False}
    )

    assert response.status_code == 200
    page = response.json()
    assert [item["id"] for item in page["items"]] == [newest.id, older.id]
    assert page["total"] == 2
    assert page["items"][0]["evaluation"] == "pending"
    assert page["items"][1]["evaluation"] == "superseded"
    assert page["items"][1]["score"] is None


def test_every_evaluation_value_is_served_with_the_scores_of_a_scored_row(
    api_client: TestClient, db_session: Session
) -> None:
    ledger = _build_ledger(db_session)

    response = api_client.get(
        "/v1/predictions", params={"season": ledger.season.code, "latest_only": False}
    )

    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 6
    by_id = {item["id"]: item for item in page["items"]}
    assert by_id[ledger.pending]["evaluation"] == "pending"
    assert by_id[ledger.pending]["score"] is None
    assert by_id[ledger.scored]["evaluation"] == "scored"
    assert by_id[ledger.superseded]["evaluation"] == "superseded"
    assert by_id[ledger.superseded]["score"] is None
    assert by_id[ledger.late]["evaluation"] == "late"
    assert by_id[ledger.late]["score"] is None
    assert by_id[ledger.withdrawn]["evaluation"] == "withdrawn"
    assert by_id[ledger.withdrawn]["score"] is None

    score = by_id[ledger.scored]["score"]
    assert isinstance(score, dict)
    assert set(score) == {"log_loss", "brier", "rps", "correct"}
    assert isinstance(score["correct"], bool)
    assert score["correct"] is True
    for metric in ("log_loss", "brier", "rps"):
        assert isinstance(score[metric], float)
        assert score[metric] >= 0.0


def test_the_evaluation_filter_selects_each_outcome_and_narrows_the_projection(
    api_client: TestClient, db_session: Session
) -> None:
    ledger = _build_ledger(db_session)

    def served(**params: str | int | bool) -> dict[str, Any]:
        response = api_client.get(
            "/v1/predictions", params={"season": ledger.season.code, **params}
        )
        assert response.status_code == 200
        page = response.json()
        return {"ids": [item["id"] for item in page["items"]], "total": page["total"]}

    pending = served(evaluation="pending")
    superseded_standing = served(evaluation="superseded")
    superseded_ledger = served(evaluation="superseded", latest_only=False)
    scored = served(evaluation="scored")
    late = served(evaluation="late")
    withdrawn = served(evaluation="withdrawn")

    assert ledger.pending in pending["ids"]
    assert ledger.superseded not in pending["ids"]
    assert superseded_standing == {"ids": [], "total": 0}
    assert superseded_ledger == {"ids": [ledger.superseded], "total": 1}
    assert scored == {"ids": [ledger.scored], "total": 1}
    assert late == {"ids": [ledger.late], "total": 1}
    assert withdrawn == {"ids": [ledger.withdrawn], "total": 1}


def test_season_match_and_team_each_narrow_the_page_and_combine_with_and(
    api_client: TestClient, db_session: Session
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    other = create_season(db_session, code="2097-98", start_year=2097)
    run = create_prediction_run(db_session)
    early = _fixture(db_session, season, kickoff_date=today + _days(2), tag="early")
    late = _fixture(db_session, season, kickoff_date=today + _days(9), tag="late")
    elsewhere = _fixture(db_session, other, kickoff_date=today + _days(3), tag="elsewhere")
    early_row = create_prediction(db_session, match=early, run=run)
    late_row = create_prediction(db_session, match=late, run=run)
    elsewhere_row = create_prediction(db_session, match=elsewhere, run=run)

    by_season = _page(api_client, {"season": season.code})
    by_match = _page(api_client, {"season": season.code, "match_id": early.id})
    by_home_team = _page(api_client, {"season": season.code, "team_id": late.home_team_id})
    by_away_team = _page(api_client, {"season": season.code, "team_id": early.away_team_id})
    other_season = _page(api_client, {"season": other.code})

    assert by_season["ids"] == [early_row.id, late_row.id]
    assert by_season["total"] == 2
    assert by_match == {"ids": [early_row.id], "total": 1}
    assert by_home_team == {"ids": [late_row.id], "total": 1}
    assert by_away_team == {"ids": [early_row.id], "total": 1}
    assert other_season == {"ids": [elsewhere_row.id], "total": 1}


def test_model_and_match_status_each_narrow_the_page(
    api_client: TestClient, db_session: Session
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    run = create_prediction_run(db_session)
    upcoming = _fixture(db_session, season, kickoff_date=today + _days(3), tag="upcoming")
    finished = _fixture(db_session, season, kickoff_date=today + _days(6), tag="finished")
    home_win = create_prediction(db_session, match=upcoming, model="home_win", run=run)
    dixon_coles = create_prediction(db_session, match=upcoming, model="dixon_coles", run=run)
    played = create_prediction(db_session, match=finished, model="home_win", run=run)
    mark_played(db_session, finished, home_goals=1, away_goals=0)

    by_model = _page(api_client, {"season": season.code, "model": "dixon_coles"})
    scheduled = _page(api_client, {"season": season.code, "match_status": "scheduled"})
    played_only = _page(api_client, {"season": season.code, "match_status": "played"})
    combined = _page(
        api_client, {"season": season.code, "model": "home_win", "match_status": "played"}
    )

    assert by_model == {"ids": [dixon_coles.id], "total": 1}
    assert scheduled == {"ids": [home_win.id, dixon_coles.id], "total": 2}
    assert played_only == {"ids": [played.id], "total": 1}
    assert combined == {"ids": [played.id], "total": 1}


def test_the_kickoff_date_range_is_inclusive_and_combines_with_and(
    api_client: TestClient, db_session: Session
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    run = create_prediction_run(db_session)
    early = _fixture(db_session, season, kickoff_date=today + _days(2), tag="range-early")
    late = _fixture(db_session, season, kickoff_date=today + _days(8), tag="range-late")
    early_row = create_prediction(db_session, match=early, run=run)
    late_row = create_prediction(db_session, match=late, run=run)

    from_bound = _page(
        api_client, {"season": season.code, "date_from": late.kickoff_date.isoformat()}
    )
    to_bound = _page(api_client, {"season": season.code, "date_to": early.kickoff_date.isoformat()})
    both = _page(
        api_client,
        {
            "season": season.code,
            "date_from": early.kickoff_date.isoformat(),
            "date_to": early.kickoff_date.isoformat(),
        },
    )

    assert from_bound == {"ids": [late_row.id], "total": 1}
    assert to_bound == {"ids": [early_row.id], "total": 1}
    assert both == {"ids": [early_row.id], "total": 1}


def test_limit_and_offset_page_the_same_ordered_list(
    api_client: TestClient, db_session: Session
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    run = create_prediction_run(db_session)
    stored = [
        create_prediction(
            db_session,
            match=_fixture(db_session, season, kickoff_date=today + _days(day), tag=f"page-{day}"),
            run=run,
        )
        for day in (2, 3, 4)
    ]

    whole = _page(api_client, {"season": season.code})
    window = _page(api_client, {"season": season.code, "limit": 1, "offset": 1})

    assert whole["ids"] == [row.id for row in stored]
    assert whole["total"] == 3
    assert window["ids"] == [stored[1].id]
    assert window["total"] == 3
    page = api_client.get(
        "/v1/predictions", params={"season": season.code, "limit": 1, "offset": 1}
    ).json()
    assert page["limit"] == 1
    assert page["offset"] == 1


def test_the_order_is_kickoff_date_then_match_then_model_then_as_of_desc(
    api_client: TestClient, db_session: Session
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    run = create_prediction_run(db_session)
    first = _fixture(db_session, season, kickoff_date=today + _days(2), tag="order-first")
    second = _fixture(db_session, season, kickoff_date=today + _days(3), tag="order-second")
    first_home_win = create_prediction(db_session, match=first, model="home_win", run=run)
    second_home_win = create_prediction(db_session, match=second, model="home_win", run=run)
    second_dixon_coles = create_prediction(db_session, match=second, model="dixon_coles", run=run)
    second_replaced = create_ledger_prediction(
        db_session, match=second, model="home_win", as_of=today - _days(1), run=run
    )

    response = api_client.get(
        "/v1/predictions", params={"season": season.code, "latest_only": False}
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [
        first_home_win.id,
        second_home_win.id,
        second_replaced.id,
        second_dixon_coles.id,
    ]
    assert [item["match"]["kickoff_date"] for item in items] == sorted(
        item["match"]["kickoff_date"] for item in items
    )


def test_get_a_prediction_serves_its_run_its_config_and_the_group_correct_evaluation(
    api_client: TestClient, db_session: Session
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    run = create_prediction_run(db_session)
    match = _fixture(db_session, season, kickoff_date=today + _days(3), tag="detail")
    older = create_ledger_prediction(
        db_session,
        match=match,
        model="home_win",
        as_of=today - _days(1),
        model_config={"seed": 0},
        odds_source="pinnacle",
        run=run,
    )
    newest = create_prediction(
        db_session, match=match, model="home_win", model_config={"seed": 0}, run=run
    )
    mark_played(db_session, match, home_goals=1, away_goals=0)

    replaced = api_client.get(f"/v1/predictions/{older.id}")
    served = api_client.get(f"/v1/predictions/{newest.id}")

    assert replaced.status_code == 200
    replaced_detail = replaced.json()
    assert set(replaced_detail) == DETAIL_FIELDS
    assert replaced_detail["id"] == older.id
    assert replaced_detail["evaluation"] == "superseded"
    assert replaced_detail["score"] is None
    assert replaced_detail["run_id"] == str(run.id)
    assert replaced_detail["model_config"] == {"seed": 0}
    assert replaced_detail["odds_source"] == "pinnacle"

    assert served.status_code == 200
    detail = served.json()
    assert set(detail) == DETAIL_FIELDS
    assert detail["id"] == newest.id
    assert detail["evaluation"] == "scored"
    assert detail["match"]["id"] == match.id
    assert detail["probabilities"] == {"home": 0.5, "draw": 0.3, "away": 0.2}
    assert detail["score"]["correct"] is True
    assert detail["run_id"] == str(run.id)


def test_get_an_unknown_prediction_is_not_found(
    api_client: TestClient, db_session: Session
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    stored = create_prediction(
        db_session,
        match=_fixture(db_session, season, kickoff_date=today + _days(3), tag="unknown"),
    )
    unknown = stored.id + 1_000_000_000

    response = api_client.get(f"/v1/predictions/{unknown}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Not Found"
    assert problem["status"] == 404
    assert problem["detail"] == "Prediction not found"
    assert problem["errors"] is None
    assert str(unknown) not in response.text


def test_a_non_integer_prediction_id_is_unprocessable_without_echoing_the_value(
    api_client: TestClient,
) -> None:
    response = api_client.get("/v1/predictions/not-a-prediction")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Unprocessable Content"
    assert problem["status"] == 422
    assert problem["detail"] == "Request validation failed"
    errors = problem["errors"]
    assert errors
    assert ["path", "prediction_id"] in [error["loc"] for error in errors]
    assert "not-a-prediction" not in response.text


def test_an_unknown_season_match_or_team_is_not_found(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    unknown = 2_147_483_647
    cases: list[tuple[dict[str, str | int], str, str]] = [
        ({"season": "1999-00"}, "Season not found", "1999-00"),
        ({"season": season.code, "match_id": unknown}, "Match not found", str(unknown)),
        ({"season": season.code, "team_id": unknown}, "Team not found", str(unknown)),
    ]

    for params, detail, sent in cases:
        response = api_client.get("/v1/predictions", params=params)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
        problem = response.json()
        assert problem["title"] == "Not Found"
        assert problem["status"] == 404
        assert problem["detail"] == detail
        assert problem["errors"] is None
        assert sent not in response.text


@pytest.mark.parametrize(
    ("params", "location", "sent"),
    [
        (
            {"date_from": "2026-05-02", "date_to": "2026-05-01"},
            ["query", "date_to"],
            "2026-05-01",
        ),
        ({"limit": 501}, ["query", "limit"], "501"),
        ({"offset": -1}, ["query", "offset"], "-1"),
        ({"model": "understat"}, ["query", "model"], "understat"),
        ({"evaluation": "unknown"}, ["query", "evaluation"], "unknown"),
        ({"date_from": "not-a-date"}, ["query", "date_from"], "not-a-date"),
    ],
)
def test_a_rejected_request_is_unprocessable_without_echoing_the_value(
    api_client: TestClient,
    params: dict[str, str | int],
    location: list[str],
    sent: str,
) -> None:
    response = api_client.get("/v1/predictions", params=params)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Unprocessable Content"
    assert problem["status"] == 422
    assert problem["detail"] == "Request validation failed"
    errors = problem["errors"]
    assert errors
    assert isinstance(errors[0]["msg"], str) and errors[0]["msg"]
    assert location in [error["loc"] for error in errors]
    assert sent not in response.text


def test_both_paths_are_served_without_a_trailing_slash(
    api_client: TestClient, db_session: Session
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    stored = create_prediction(
        db_session,
        match=_fixture(db_session, season, kickoff_date=today + _days(3), tag="slash"),
    )

    collection = api_client.get(
        "/v1/predictions", params={"season": season.code}, follow_redirects=False
    )
    single = api_client.get(f"/v1/predictions/{stored.id}", follow_redirects=False)

    assert collection.status_code == 200
    assert single.status_code == 200


def test_the_default_season_is_the_latest_one_with_a_match(
    api_client: TestClient, db_session: Session
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    create_season(db_session, code="2099-00", start_year=2099)
    run = create_prediction_run(db_session)
    stored = create_prediction(
        db_session,
        match=_fixture(db_session, season, kickoff_date=today + _days(3), tag="default-season"),
        run=run,
    )

    response = api_client.get("/v1/predictions")

    assert response.status_code == 200
    items = response.json()["items"]
    assert stored.id in [item["id"] for item in items]
    assert [item["match"]["season"] for item in items] == [season.code] * len(items)


def test_the_brief_reader_returns_the_standing_row_of_each_group(
    db_session: Session,
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    run = create_prediction_run(db_session)
    match = _fixture(db_session, season, kickoff_date=today + _days(3), tag="brief")
    dixon_coles = create_prediction(
        db_session, match=match, model="dixon_coles", model_version="2.0", run=run
    )
    replaced = create_ledger_prediction(
        db_session, match=match, model="home_win", as_of=today - _days(1), run=run
    )
    standing = create_prediction(db_session, match=match, model="home_win", run=run)

    stored = views.load_stored_predictions(db_session, match_ids=[match.id])
    briefs = views.prediction_briefs(
        evaluate([entry.row for entry in stored]),
        prediction_ids={entry.identity: entry.id for entry in stored},
    )

    assert [brief.prediction_id for brief in briefs] == [standing.id, dixon_coles.id]
    assert [brief.model for brief in briefs] == ["home_win", "dixon_coles"]
    assert [brief.model_version for brief in briefs] == ["1.0", "2.0"]
    assert briefs[0].evaluation == "pending"
    assert briefs[0].probabilities.home == 0.5
    assert replaced.id not in [brief.prediction_id for brief in briefs]


def test_openapi_documents_the_predictions_contract() -> None:
    document = create_app().openapi()
    collection = document["paths"]["/v1/predictions"]["get"]
    single = document["paths"]["/v1/predictions/{prediction_id}"]["get"]

    assert collection["operationId"] == "list_predictions"
    assert single["operationId"] == "get_prediction"

    page_schema = collection["responses"]["200"]["content"]["application/json"]["schema"]
    assert page_schema == {"$ref": PREDICTION_PAGE_REF}
    detail_schema = single["responses"]["200"]["content"]["application/json"]["schema"]
    assert detail_schema == {"$ref": PREDICTION_DETAIL_REF}

    schemas = document["components"]["schemas"]
    assert schemas["PredictionPage"]["properties"]["items"]["items"]["$ref"] == PREDICTION_OUT_REF
    assert "model_config" in schemas["PredictionDetail"]["properties"]

    for operation in (collection, single):
        for status in ("404", "422"):
            content = operation["responses"][status]["content"]
            assert PROBLEM_MEDIA_TYPE in content
            assert any(media.get("schema") == {"$ref": PROBLEM_REF} for media in content.values())

    assert "HTTPValidationError" not in schemas


def _days(count: int) -> datetime.timedelta:
    """Return ``count`` whole days as a timedelta."""
    return datetime.timedelta(days=count)


def _fixture(session: Session, season: Season, *, kickoff_date: datetime.date, tag: str) -> Match:
    """Insert one scheduled fixture of ``season`` with a pair of synthetic teams."""
    home = create_team(session, name=f"Synthetic Home {tag}")
    away = create_team(session, name=f"Synthetic Away {tag}")
    return create_match(session, season=season, home=home, away=away, kickoff_date=kickoff_date)


def _page(api_client: TestClient, params: dict[str, str | int | bool]) -> dict[str, Any]:
    """Return the identifiers and the total of one successful collection request."""
    response = api_client.get("/v1/predictions", params=params)
    assert response.status_code == 200
    page = response.json()
    return {"ids": [item["id"] for item in page["items"]], "total": page["total"]}


def _as_datetime(value: Any) -> datetime.datetime:
    """Return a serialised timestamp as the datetime it spells."""
    assert isinstance(value, str), f"the payload timestamp is not a string: {value!r}"
    return datetime.datetime.fromisoformat(value)


def _build_ledger(session: Session) -> Ledger:
    """Store one prediction for each evaluation a stored row can earn.

    A season of five fixtures: one still to be played, one played, one with a
    two-day ledger whose newest row stands, one moved to yesterday and played
    after its prediction was stored, and one withdrawn by its source.
    """
    today = madrid_today(session)
    season = create_season(session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    run = create_prediction_run(session)

    pending_match = _fixture(session, season, kickoff_date=today + _days(3), tag="ledger-pending")
    pending = create_prediction(session, match=pending_match, run=run)

    scored_match = _fixture(session, season, kickoff_date=today + _days(4), tag="ledger-scored")
    scored = create_prediction(session, match=scored_match, run=run)
    mark_played(session, scored_match, home_goals=2, away_goals=1)

    ledger_match = _fixture(session, season, kickoff_date=today + _days(5), tag="ledger-row")
    superseded = create_ledger_prediction(
        session, match=ledger_match, as_of=today - _days(1), run=run
    )
    create_prediction(session, match=ledger_match, run=run)

    late_match = _fixture(session, season, kickoff_date=today + _days(6), tag="ledger-late")
    late = create_prediction(session, match=late_match, run=run)
    mark_played(session, late_match, home_goals=0, away_goals=2)
    move_match(session, late_match, kickoff_date=today - _days(1))

    withdrawn_match = _fixture(
        session, season, kickoff_date=today + _days(7), tag="ledger-withdrawn"
    )
    withdrawn = create_prediction(session, match=withdrawn_match, run=run)
    mark_withdrawn(session, withdrawn_match)

    return Ledger(
        season=season,
        pending=pending.id,
        scored=scored.id,
        superseded=superseded.id,
        late=late.id,
        withdrawn=withdrawn.id,
    )
