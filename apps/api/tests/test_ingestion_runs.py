"""Contract tests for the ``/v1/ingestion-runs`` endpoints on a disposable database.

The suite shares its database with the runs the real ingestion left behind, so
every collection test bounds the page with the synthetic season code it created
and asserts on the runs it inserted, never on the absolute size of the table.
The season comes from :func:`tests.factories.create_season` and the runs from
:mod:`tests.ingestion_runs_support`, so nothing here depends on data a source
produced.
"""

import datetime
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from lmi_api.main import create_app
from lmi_core.db.models import IngestionRun
from sqlalchemy.orm import Session

from tests.factories import create_season
from tests.ingestion_runs_support import create_ingestion_run, create_rejections

pytestmark = pytest.mark.integration

RUN_PAGE_REF = "#/components/schemas/IngestionRunPage"
RUN_OUT_REF = "#/components/schemas/IngestionRunOut"
RUN_DETAIL_REF = "#/components/schemas/IngestionRunDetail"
PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type every error response is sent with."""

RUN_FIELDS = {
    "id",
    "source",
    "season_code",
    "loader_version",
    "started_at",
    "finished_at",
    "status",
    "rows_read",
    "rows_upserted",
    "rows_removed",
    "rows_withdrawn",
    "rows_rejected",
    "content_sha256",
    "error",
}
"""Every field a run payload carries, whatever the level of detail."""


def assert_run_payload(item: dict[str, Any], run: IngestionRun) -> None:
    """Assert one payload row carries the stored value of every run field.

    ``item`` is the decoded JSON object, so the identifier is a string and the
    two timestamps are ISO 8601 strings: the identifier is compared in that same
    spelling and the timestamps are parsed back to aware datetimes. The check is
    a subset of the payload, because the detail adds two fields of its own,
    which its own test asserts.
    """
    assert set(item) >= RUN_FIELDS
    assert item["id"] == str(run.id)
    assert item["source"] == run.source
    assert item["season_code"] == run.season_code
    assert item["loader_version"] == run.loader_version
    assert _as_datetime(item["started_at"]) == run.started_at
    if run.finished_at is None:
        assert item["finished_at"] is None
    else:
        assert _as_datetime(item["finished_at"]) == run.finished_at
    assert item["status"] == run.status
    assert item["rows_read"] == run.rows_read
    assert item["rows_upserted"] == run.rows_upserted
    assert item["rows_removed"] == run.rows_removed
    assert item["rows_withdrawn"] == run.rows_withdrawn
    assert item["rows_rejected"] == run.rows_rejected
    assert item["content_sha256"] == run.content_sha256
    assert item["error"] == run.error


def _as_datetime(value: Any) -> datetime.datetime:
    """Return a serialised timestamp as the datetime it spells."""
    assert isinstance(value, str), f"the payload timestamp is not a string: {value!r}"
    return datetime.datetime.fromisoformat(value)


def _ids(items: list[Any]) -> list[str]:
    """Return the identifiers of a payload row list, in the order it arrived."""
    return [item["id"] for item in items]


def test_list_ingestion_runs_of_a_season_returns_them_newest_first(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    oldest = create_ingestion_run(
        db_session,
        started_at=datetime.datetime(2098, 8, 1, 10, 0, tzinfo=datetime.UTC),
        season_code=season.code,
        rows_read=40,
        rows_upserted=38,
        rows_removed=1,
        rows_withdrawn=1,
        rows_rejected=2,
        content_sha256="a" * 64,
    )
    middle = create_ingestion_run(
        db_session,
        started_at=datetime.datetime(2098, 8, 2, 10, 0, tzinfo=datetime.UTC),
        season_code=season.code,
        source="openfootball",
        status="running",
    )
    newest = create_ingestion_run(
        db_session,
        started_at=datetime.datetime(2098, 8, 3, 10, 0, tzinfo=datetime.UTC),
        season_code=season.code,
        status="failed",
        error="the archive could not be downloaded",
    )

    response = api_client.get("/v1/ingestion-runs", params={"season": season.code, "limit": 3})

    assert response.status_code == 200
    page = response.json()
    assert _ids(page["items"]) == [str(newest.id), str(middle.id), str(oldest.id)]
    assert set(page["items"][0]) == RUN_FIELDS
    assert page["total"] == 3
    assert page["limit"] == 3
    assert page["offset"] == 0
    for item, run in zip(page["items"], (newest, middle, oldest), strict=True):
        assert_run_payload(item, run)


def test_list_ingestion_runs_breaks_a_started_at_tie_by_identifier_descending(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    started_at = datetime.datetime(2098, 8, 1, 10, 0, tzinfo=datetime.UTC)
    first = create_ingestion_run(db_session, started_at=started_at, season_code=season.code)
    second = create_ingestion_run(db_session, started_at=started_at, season_code=season.code)

    response = api_client.get("/v1/ingestion-runs", params={"season": season.code})

    assert response.status_code == 200
    assert _ids(response.json()["items"]) == sorted((str(first.id), str(second.id)), reverse=True)


def test_limit_and_offset_page_the_same_ordered_list(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    created = [
        create_ingestion_run(
            db_session,
            started_at=datetime.datetime(2098, 8, day, 10, 0, tzinfo=datetime.UTC),
            season_code=season.code,
        )
        for day in (1, 2, 3)
    ]

    whole = api_client.get("/v1/ingestion-runs", params={"season": season.code})
    window = api_client.get(
        "/v1/ingestion-runs",
        params={"season": season.code, "limit": 1, "offset": 1},
    )

    assert whole.status_code == 200
    assert window.status_code == 200
    whole_page = whole.json()
    window_page = window.json()
    assert _ids(whole_page["items"]) == [str(run.id) for run in reversed(created)]
    assert window_page["items"] == [whole_page["items"][1]]
    assert window_page["total"] == whole_page["total"] == 3
    assert window_page["limit"] == 1
    assert window_page["offset"] == 1


def test_source_season_and_status_each_narrow_the_page_and_combine_with_and(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    other_season = create_season(db_session, code="2099-00", start_year=2099)
    succeeded = create_ingestion_run(
        db_session,
        started_at=datetime.datetime(2098, 8, 1, 10, 0, tzinfo=datetime.UTC),
        season_code=season.code,
        source="football_data",
        status="succeeded",
    )
    failed = create_ingestion_run(
        db_session,
        started_at=datetime.datetime(2098, 8, 2, 10, 0, tzinfo=datetime.UTC),
        season_code=season.code,
        source="openfootball",
        status="failed",
    )
    running = create_ingestion_run(
        db_session,
        started_at=datetime.datetime(2098, 8, 3, 10, 0, tzinfo=datetime.UTC),
        season_code=season.code,
        source="football_data",
        status="running",
    )
    elsewhere = create_ingestion_run(
        db_session,
        started_at=datetime.datetime(2098, 8, 4, 10, 0, tzinfo=datetime.UTC),
        season_code=other_season.code,
        source="football_data",
        status="succeeded",
    )

    by_season = api_client.get("/v1/ingestion-runs", params={"season": season.code})
    by_source = api_client.get("/v1/ingestion-runs", params={"source": "openfootball"})
    by_status = api_client.get("/v1/ingestion-runs", params={"status": "running"})
    combined = api_client.get(
        "/v1/ingestion-runs",
        params={"season": season.code, "source": "football_data", "status": "succeeded"},
    )

    assert by_season.status_code == 200
    season_page = by_season.json()
    assert _ids(season_page["items"]) == [str(running.id), str(failed.id), str(succeeded.id)]
    assert season_page["total"] == 3
    assert str(elsewhere.id) not in _ids(season_page["items"])

    assert by_source.status_code == 200
    source_ids = _ids(by_source.json()["items"])
    assert str(failed.id) in source_ids
    assert str(succeeded.id) not in source_ids
    assert str(running.id) not in source_ids

    assert by_status.status_code == 200
    status_ids = _ids(by_status.json()["items"])
    assert str(running.id) in status_ids
    assert str(succeeded.id) not in status_ids

    assert combined.status_code == 200
    combined_page = combined.json()
    assert _ids(combined_page["items"]) == [str(succeeded.id)]
    assert combined_page["total"] == 1


def test_a_stored_season_without_runs_of_the_requested_source_is_an_empty_page(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    create_ingestion_run(
        db_session,
        started_at=datetime.datetime(2098, 8, 1, 10, 0, tzinfo=datetime.UTC),
        season_code=season.code,
        source="football_data",
    )

    response = api_client.get(
        "/v1/ingestion-runs",
        params={"season": season.code, "source": "openfootball"},
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_list_ingestion_runs_of_an_unknown_season_is_not_found(api_client: TestClient) -> None:
    response = api_client.get("/v1/ingestion-runs", params={"season": "1999-00"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Not Found"
    assert problem["status"] == 404
    assert problem["detail"] == "Season not found"
    assert problem["errors"] is None
    assert "1999-00" not in response.text


@pytest.mark.parametrize(
    ("path", "params", "location", "sent"),
    [
        ("/v1/ingestion-runs/not-a-run", {}, ["path", "run_id"], "not-a-run"),
        ("/v1/ingestion-runs", {"limit": 201}, ["query", "limit"], "201"),
        ("/v1/ingestion-runs", {"offset": -1}, ["query", "offset"], "-1"),
        ("/v1/ingestion-runs", {"source": "understat"}, ["query", "source"], "understat"),
    ],
)
def test_a_rejected_request_is_unprocessable_without_echoing_the_value(
    api_client: TestClient,
    path: str,
    params: dict[str, str | int],
    location: list[str],
    sent: str,
) -> None:
    response = api_client.get(path, params=params)

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


def test_get_an_ingestion_run_returns_its_counters_and_rejections(
    api_client: TestClient, db_session: Session
) -> None:
    run = create_ingestion_run(
        db_session,
        started_at=datetime.datetime(2098, 8, 1, 10, 0, tzinfo=datetime.UTC),
        finished_at=datetime.datetime(2098, 8, 1, 10, 5, tzinfo=datetime.UTC),
        rows_read=120,
        rows_upserted=118,
        rows_removed=1,
        rows_withdrawn=1,
        rows_rejected=3,
        content_sha256="b" * 64,
    )
    create_rejections(db_session, run, count=3)

    response = api_client.get(f"/v1/ingestion-runs/{run.id}")

    assert response.status_code == 200
    detail = response.json()
    assert_run_payload(detail, run)
    assert detail["rejections_total"] == 3
    assert detail["rejections"] == [
        {"row_number": 1, "reason": "unparsable score", "raw": {"line": 1}},
        {"row_number": 2, "reason": "unparsable score", "raw": {"line": 2}},
        {"row_number": 3, "reason": "unparsable score", "raw": {"line": 3}},
    ]


def test_get_an_ingestion_run_caps_the_embedded_rejections(
    api_client: TestClient, db_session: Session
) -> None:
    run = create_ingestion_run(
        db_session,
        started_at=datetime.datetime(2098, 8, 1, 10, 0, tzinfo=datetime.UTC),
    )
    create_rejections(db_session, run, count=205, reason="unknown team")

    response = api_client.get(f"/v1/ingestion-runs/{run.id}")

    assert response.status_code == 200
    detail = response.json()
    assert detail["rejections_total"] == 205
    assert [rejection["row_number"] for rejection in detail["rejections"]] == list(range(1, 201))


def test_get_an_unknown_ingestion_run_is_not_found(api_client: TestClient) -> None:
    run_id = uuid.uuid4()

    response = api_client.get(f"/v1/ingestion-runs/{run_id}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Not Found"
    assert problem["status"] == 404
    assert problem["detail"] == "Ingestion run not found"
    assert problem["errors"] is None
    assert str(run_id) not in response.text


def test_both_paths_are_served_without_a_trailing_slash(
    api_client: TestClient, db_session: Session
) -> None:
    run = create_ingestion_run(
        db_session,
        started_at=datetime.datetime(2098, 8, 1, 10, 0, tzinfo=datetime.UTC),
    )

    collection = api_client.get("/v1/ingestion-runs", follow_redirects=False)
    single = api_client.get(f"/v1/ingestion-runs/{run.id}", follow_redirects=False)

    assert collection.status_code == 200
    assert single.status_code == 200


def test_openapi_documents_the_ingestion_runs_contract() -> None:
    document = create_app().openapi()
    collection = document["paths"]["/v1/ingestion-runs"]["get"]
    single = document["paths"]["/v1/ingestion-runs/{run_id}"]["get"]

    assert collection["operationId"] == "list_ingestion_runs"
    assert single["operationId"] == "get_ingestion_run"

    page_schema = collection["responses"]["200"]["content"]["application/json"]["schema"]
    assert page_schema == {"$ref": RUN_PAGE_REF}
    detail_schema = single["responses"]["200"]["content"]["application/json"]["schema"]
    assert detail_schema == {"$ref": RUN_DETAIL_REF}

    schemas = document["components"]["schemas"]
    assert schemas["IngestionRunPage"]["properties"]["items"]["items"]["$ref"] == RUN_OUT_REF

    for operation in (collection, single):
        for status in ("404", "422"):
            content = operation["responses"][status]["content"]
            assert PROBLEM_MEDIA_TYPE in content
            assert any(media.get("schema") == {"$ref": PROBLEM_REF} for media in content.values())

    assert "HTTPValidationError" not in schemas
