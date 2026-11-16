"""Contract tests for the problem details error handling, with no database.

Nothing in this module opens a session, and it carries no ``integration``
marker, so it runs in ``make test`` on a machine with no PostgreSQL: the
handlers are the same whatever the endpoints above them do, and the routes built
here exist only to raise.

The subject is one application with three extra test-only routes: one that
raises the 404 an endpoint raises, one with a validated query parameter, and one
that raises an unexpected error whose message carries a connection string. The
last one is what proves the response body discloses nothing.
"""

import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from lmi_api.main import create_app
from lmi_api.schemas.common import Page, Problem
from lmi_api.schemas.teams import Team

PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type every error response is sent with."""

FAKE_DATABASE_URL = "postgresql+psycopg://placeholder:placeholder@db.invalid:5432/lmi"
"""A connection string that names no real host: it only has to be recognisable."""


def _application_with_failing_routes() -> FastAPI:
    """Return the application with three routes that exercise the handlers.

    The routes are added to a freshly built application rather than to the
    module-level one, so adding them cannot affect ``test_health.py`` or the
    OpenAPI document another test asserts on.
    """
    application = create_app()

    @application.get("/test/missing-season")
    def raise_missing_season() -> None:
        raise HTTPException(status_code=404, detail="Season 'x' not found")

    @application.get("/test/validated")
    def read_limit(limit: int = 0) -> dict[str, int]:
        return {"limit": limit}

    @application.get("/test/unhandled")
    def raise_unhandled() -> None:
        raise RuntimeError(f"could not connect to {FAKE_DATABASE_URL}")

    return application


def test_problem_body_has_exactly_the_five_rfc_9457_fields() -> None:
    problem = Problem(title="Not Found", status=404)

    assert problem.model_dump() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": None,
        "errors": None,
    }


def test_page_envelope_keeps_its_four_fields_and_types_its_items() -> None:
    page = Page[Team](items=[Team(id=1, name="Test FC Alpha")], total=1, limit=10, offset=0)

    assert page.model_dump() == {
        "items": [{"id": 1, "name": "Test FC Alpha"}],
        "total": 1,
        "limit": 10,
        "offset": 0,
    }

    parsed = Page[Team].model_validate(
        {
            "items": [{"id": 2, "name": "Test FC Beta"}],
            "total": 1,
            "limit": 10,
            "offset": 0,
        }
    )
    assert isinstance(parsed.items[0], Team)
    assert parsed.items[0].name == "Test FC Beta"


def test_a_raised_http_exception_becomes_a_problem_details_body() -> None:
    application = _application_with_failing_routes()

    with TestClient(application) as client:
        response = client.get("/test/missing-season")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "Season 'x' not found",
        "errors": None,
    }


def test_a_method_not_allowed_keeps_the_allow_header_of_the_status() -> None:
    """A problem body must not cost the client a header the status carries.

    The application raises this 405 itself, for a path that only serves another
    method, and that exception carries the ``Allow`` header. A client reads the
    allowed methods from the response instead of guessing them, so the handler
    has to forward the header of the exception it describes.
    """
    application = _application_with_failing_routes()

    with TestClient(application) as client:
        response = client.post("/test/validated")

    assert response.status_code == 405
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["status"] == 405
    assert problem["title"] == "Method Not Allowed"
    assert "GET" in response.headers["allow"]


def test_a_rejected_query_parameter_is_described_without_echoing_its_value() -> None:
    application = _application_with_failing_routes()

    with TestClient(application) as client:
        response = client.get("/test/validated", params={"limit": "not-a-number"})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["type"] == "about:blank"
    assert problem["title"] == "Unprocessable Content"
    assert problem["status"] == 422
    assert problem["detail"] == "Request validation failed"
    errors = problem["errors"]
    assert len(errors) == 1
    assert errors[0]["loc"] == ["query", "limit"]
    assert isinstance(errors[0]["msg"], str) and errors[0]["msg"]
    assert isinstance(errors[0]["type"], str) and errors[0]["type"]
    assert "not-a-number" not in response.text


def test_an_unhandled_error_is_logged_and_answered_without_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = _application_with_failing_routes()

    with (
        TestClient(application, raise_server_exceptions=False) as client,
        caplog.at_level(logging.ERROR),
    ):
        response = client.get("/test/unhandled")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json() == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "detail": None,
        "errors": None,
    }
    assert FAKE_DATABASE_URL not in response.text
    assert "could not connect" not in response.text

    messages = [record.getMessage() for record in caplog.records if record.levelno == logging.ERROR]
    assert any("GET" in message and "/test/unhandled" in message for message in messages)
