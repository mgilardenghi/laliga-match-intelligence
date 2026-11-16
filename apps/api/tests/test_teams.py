"""Contract tests for the ``/v1/teams`` endpoints against a disposable database.

The suite shares its database with the seeded teams and the ingested matches, so
the tests assert on the synthetic rows they inserted and on invariants of the
collection, never on its absolute size. A season code in the far future bounds
every filter to the rows of the test.
"""

import pytest
from fastapi.testclient import TestClient
from lmi_api.main import create_app
from sqlalchemy.orm import Session

from tests.factories import create_match, create_season, create_team

pytestmark = pytest.mark.integration

TEAM_ITEM_REF = "#/components/schemas/Team"
PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type every error response is sent with."""


def test_list_teams_returns_every_team_by_name(api_client: TestClient, db_session: Session) -> None:
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")

    response = api_client.get("/v1/teams")

    assert response.status_code == 200
    teams = response.json()
    assert {"id": alpha.id, "name": "Test FC Alpha"} in teams
    assert {"id": beta.id, "name": "Test FC Beta"} in teams
    names = [team["name"] for team in teams]
    assert names == sorted(names)


def test_list_teams_of_a_season_returns_only_the_teams_of_its_matches(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    home = create_team(db_session, name="Test FC Alpha")
    away = create_team(db_session, name="Test FC Beta")
    unmatched = create_team(db_session, name="Test FC Gamma")
    create_match(db_session, season=season, home=home, away=away)

    response = api_client.get("/v1/teams", params={"season": season.code})

    assert response.status_code == 200
    assert response.json() == [
        {"id": home.id, "name": "Test FC Alpha"},
        {"id": away.id, "name": "Test FC Beta"},
    ]
    assert {"id": unmatched.id, "name": "Test FC Gamma"} not in response.json()


def test_list_teams_of_a_season_without_matches_is_empty(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    create_team(db_session, name="Test FC Alpha")

    response = api_client.get("/v1/teams", params={"season": season.code})

    assert response.status_code == 200
    assert response.json() == []


def test_list_teams_of_an_unknown_season_is_not_found(api_client: TestClient) -> None:
    response = api_client.get("/v1/teams", params={"season": "1999-00"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "Season not found",
        "errors": None,
    }


def test_get_team_returns_the_team(api_client: TestClient, db_session: Session) -> None:
    team = create_team(db_session, name="Test FC Alpha")

    response = api_client.get(f"/v1/teams/{team.id}")

    assert response.status_code == 200
    assert response.json() == {"id": team.id, "name": "Test FC Alpha"}


def test_get_an_unknown_team_is_not_found(api_client: TestClient) -> None:
    response = api_client.get("/v1/teams/9999999")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["type"] == "about:blank"
    assert problem["title"] == "Not Found"
    assert problem["status"] == 404
    assert problem["detail"] == "Team not found"
    assert problem["errors"] is None


def test_get_a_team_with_a_non_integer_id_is_rejected_without_echoing_it(
    api_client: TestClient,
) -> None:
    response = api_client.get("/v1/teams/not-a-team")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["type"] == "about:blank"
    assert problem["title"] == "Unprocessable Content"
    assert problem["status"] == 422
    assert problem["detail"] == "Request validation failed"
    errors = problem["errors"]
    assert len(errors) == 1
    assert errors[0]["loc"] == ["path", "team_id"]
    assert isinstance(errors[0]["msg"], str) and errors[0]["msg"]
    assert isinstance(errors[0]["type"], str) and errors[0]["type"]
    assert "not-a-team" not in response.text


def test_list_teams_serves_the_path_without_a_trailing_slash(api_client: TestClient) -> None:
    response = api_client.get("/v1/teams", follow_redirects=False)

    assert response.status_code == 200


def test_openapi_documents_the_teams_contract() -> None:
    document = create_app().openapi()
    collection = document["paths"]["/v1/teams"]["get"]
    single = document["paths"]["/v1/teams/{team_id}"]["get"]

    assert collection["operationId"] == "list_teams"
    items = collection["responses"]["200"]["content"]["application/json"]["schema"]["items"]
    assert items["$ref"] == TEAM_ITEM_REF

    assert single["operationId"] == "get_team"
    team_schema = single["responses"]["200"]["content"]["application/json"]["schema"]
    assert team_schema["$ref"] == TEAM_ITEM_REF

    for operation in (collection, single):
        for status in ("404", "422"):
            content = operation["responses"][status]["content"]
            assert PROBLEM_MEDIA_TYPE in content
            assert any(media.get("schema") == {"$ref": PROBLEM_REF} for media in content.values())

    assert "HTTPValidationError" not in document["components"]["schemas"]
