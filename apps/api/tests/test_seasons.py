"""Contract tests for ``GET /v1/seasons`` against a disposable database.

The suite shares its database with the seeded reference seasons, so the tests
assert on the synthetic rows they inserted and on invariants of the whole
collection, never on its absolute size.
"""

import pytest
from fastapi.testclient import TestClient
from lmi_api.main import create_app
from sqlalchemy.orm import Session

from tests.factories import create_season

pytestmark = pytest.mark.integration

SEASON_ITEM_REF = "#/components/schemas/Season"
"""The schema reference the generated client models one season from."""


def test_list_seasons_returns_every_season_newest_first(
    api_client: TestClient, db_session: Session
) -> None:
    older = create_season(db_session, code="2098-99", start_year=2098)
    newer = create_season(db_session, code="2099-00", start_year=2099)

    response = api_client.get("/v1/seasons")

    assert response.status_code == 200
    seasons = response.json()
    assert {"code": older.code, "start_year": older.start_year} in seasons
    assert {"code": newer.code, "start_year": newer.start_year} in seasons
    start_years = [season["start_year"] for season in seasons]
    assert start_years == sorted(start_years, reverse=True)
    codes = [season["code"] for season in seasons]
    assert len(set(codes)) == len(codes)


def test_list_seasons_serves_the_path_without_a_trailing_slash(api_client: TestClient) -> None:
    response = api_client.get("/v1/seasons", follow_redirects=False)

    assert response.status_code == 200


def test_openapi_documents_the_seasons_contract() -> None:
    document = create_app().openapi()
    operation = document["paths"]["/v1/seasons"]["get"]

    assert operation["operationId"] == "list_seasons"
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["type"] == "array"
    assert schema["items"]["$ref"] == SEASON_ITEM_REF
    assert "404" not in operation["responses"]
    assert "422" not in operation["responses"]
    assert "HTTPValidationError" not in document["components"]["schemas"]
