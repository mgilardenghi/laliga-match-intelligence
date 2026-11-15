"""Contract tests for the ``/v1/matches`` endpoints against a disposable database.

The suite shares its database with the seeded seasons and the ingested matches,
so every collection test filters on the code of the synthetic season it inserted
and asserts on its own rows, never on the absolute size of the table. A season
code in the far future bounds the answer to the rows of the test, and within a
test the fixtures are created with the synthetic clubs of the factory.
"""

import datetime
import decimal

import pytest
from fastapi.testclient import TestClient
from lmi_api.main import create_app
from lmi_core.db.models import MatchStat
from sqlalchemy import event
from sqlalchemy.orm import Session

from tests.factories import create_match, create_season, create_team

pytestmark = pytest.mark.integration

MATCH_PAGE_REF = "#/components/schemas/MatchPage"
MATCH_SUMMARY_REF = "#/components/schemas/MatchSummary"
MATCH_DETAIL_REF = "#/components/schemas/MatchDetail"
PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type every error response is sent with."""


def test_list_matches_of_a_season_returns_only_that_season(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    other_season = create_season(db_session, code="2099-00", start_year=2099)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    gamma = create_team(db_session, name="Test FC Gamma")
    first = create_match(
        db_session,
        season=season,
        home=alpha,
        away=beta,
        kickoff_date=datetime.date(2098, 8, 15),
    )
    second = create_match(
        db_session,
        season=season,
        home=gamma,
        away=alpha,
        kickoff_date=datetime.date(2098, 8, 22),
    )
    create_match(
        db_session,
        season=other_season,
        home=beta,
        away=gamma,
        kickoff_date=datetime.date(2099, 8, 14),
    )

    response = api_client.get("/v1/matches", params={"season": season.code})

    assert response.status_code == 200
    page = response.json()
    assert [item["id"] for item in page["items"]] == [first.id, second.id]
    assert page["total"] == 2
    assert page["total"] == len(page["items"])
    assert {item["season"] for item in page["items"]} == {season.code}


def test_list_matches_of_a_season_returns_the_teams_of_each_fixture(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    create_match(db_session, season=season, home=alpha, away=beta)

    response = api_client.get("/v1/matches", params={"season": season.code})

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["home_team"] == {"id": alpha.id, "name": "Test FC Alpha"}
    assert item["away_team"] == {"id": beta.id, "name": "Test FC Beta"}


def test_list_matches_by_team_returns_the_matches_where_it_played_home_or_away(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    gamma = create_team(db_session, name="Test FC Gamma")
    at_home = create_match(
        db_session, season=season, home=alpha, away=beta, kickoff_date=datetime.date(2098, 8, 15)
    )
    away = create_match(
        db_session, season=season, home=gamma, away=alpha, kickoff_date=datetime.date(2098, 8, 22)
    )
    create_match(
        db_session, season=season, home=beta, away=gamma, kickoff_date=datetime.date(2098, 8, 29)
    )

    response = api_client.get("/v1/matches", params={"season": season.code, "team_id": alpha.id})

    assert response.status_code == 200
    page = response.json()
    assert [item["id"] for item in page["items"]] == [at_home.id, away.id]
    assert page["total"] == 2


def test_list_matches_by_matchday_returns_only_that_matchday(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    gamma = create_team(db_session, name="Test FC Gamma")
    create_match(db_session, season=season, home=alpha, away=beta, matchday=1)
    second = create_match(db_session, season=season, home=beta, away=gamma, matchday=2)

    response = api_client.get("/v1/matches", params={"season": season.code, "matchday": 2})

    assert response.status_code == 200
    page = response.json()
    assert [item["id"] for item in page["items"]] == [second.id]
    assert page["total"] == 1


def test_list_matches_by_status_returns_only_that_status(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    gamma = create_team(db_session, name="Test FC Gamma")
    create_match(db_session, season=season, home=alpha, away=beta)
    played = create_match(
        db_session,
        season=season,
        home=beta,
        away=gamma,
        status="played",
        home_goals=3,
        away_goals=0,
    )

    response = api_client.get("/v1/matches", params={"season": season.code, "status": "played"})

    assert response.status_code == 200
    page = response.json()
    assert [item["id"] for item in page["items"]] == [played.id]
    assert page["items"][0]["status"] == "played"
    assert page["items"][0]["result"] == "H"


def test_list_matches_between_two_dates_includes_both_boundaries(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    gamma = create_team(db_session, name="Test FC Gamma")
    first = create_match(
        db_session, season=season, home=alpha, away=beta, kickoff_date=datetime.date(2098, 8, 15)
    )
    last = create_match(
        db_session, season=season, home=gamma, away=alpha, kickoff_date=datetime.date(2098, 8, 22)
    )
    create_match(
        db_session, season=season, home=beta, away=gamma, kickoff_date=datetime.date(2098, 8, 29)
    )

    response = api_client.get(
        "/v1/matches",
        params={
            "season": season.code,
            "date_from": "2098-08-15",
            "date_to": "2098-08-22",
        },
    )

    assert response.status_code == 200
    page = response.json()
    assert [item["id"] for item in page["items"]] == [first.id, last.id]
    assert page["total"] == 2


def test_list_matches_sorts_by_date_then_time_and_keeps_a_missing_time_last(
    api_client: TestClient, db_session: Session
) -> None:
    """Pin the whole sort key, in both directions.

    The fixtures of the second day are created in the order ``20:30``, ``19:00``
    then no time, so their identifiers run against their kickoff times: an
    ``asc`` page that sorted the day by identifier, or that let a null time
    lead the reverse page, could not produce the sequences asserted below.
    """
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    gamma = create_team(db_session, name="Test FC Gamma")
    kickoff = datetime.date(2098, 8, 15)
    timed = create_match(
        db_session,
        season=season,
        home=alpha,
        away=beta,
        kickoff_date=kickoff,
        kickoff_time=datetime.time(18, 0),
    )
    untimed = create_match(db_session, season=season, home=beta, away=gamma, kickoff_date=kickoff)
    later_day = create_match(
        db_session,
        season=season,
        home=gamma,
        away=alpha,
        kickoff_date=datetime.date(2098, 8, 22),
        kickoff_time=datetime.time(20, 30),
    )
    earlier_that_day = create_match(
        db_session,
        season=season,
        home=alpha,
        away=gamma,
        kickoff_date=datetime.date(2098, 8, 22),
        kickoff_time=datetime.time(19, 0),
    )
    untimed_later_day = create_match(
        db_session,
        season=season,
        home=beta,
        away=alpha,
        kickoff_date=datetime.date(2098, 8, 22),
    )

    ascending = api_client.get("/v1/matches", params={"season": season.code, "order": "asc"})
    descending = api_client.get("/v1/matches", params={"season": season.code, "order": "desc"})

    assert ascending.status_code == 200
    assert [item["id"] for item in ascending.json()["items"]] == [
        timed.id,
        untimed.id,
        earlier_that_day.id,
        later_day.id,
        untimed_later_day.id,
    ]
    assert descending.status_code == 200
    assert [item["id"] for item in descending.json()["items"]] == [
        later_day.id,
        earlier_that_day.id,
        untimed_later_day.id,
        timed.id,
        untimed.id,
    ]


def test_list_matches_pages_a_full_total_with_the_requested_window(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    gamma = create_team(db_session, name="Test FC Gamma")
    pairs = [
        (alpha, beta),
        (beta, alpha),
        (alpha, gamma),
        (gamma, alpha),
        (beta, gamma),
    ]
    created = [
        create_match(
            db_session,
            season=season,
            home=home,
            away=away,
            kickoff_date=datetime.date(2098, 8, day),
        )
        for day, (home, away) in enumerate(pairs, start=1)
    ]

    response = api_client.get(
        "/v1/matches", params={"season": season.code, "limit": 2, "offset": 1}
    )

    assert response.status_code == 200
    page = response.json()
    assert page["total"] == len(created)
    assert page["limit"] == 2
    assert page["offset"] == 1
    assert [item["id"] for item in page["items"]] == [created[1].id, created[2].id]


def test_list_matches_of_an_unknown_season_is_not_found(api_client: TestClient) -> None:
    response = api_client.get("/v1/matches", params={"season": "1999-00"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Not Found"
    assert problem["status"] == 404
    assert problem["detail"] == "Season not found"
    assert problem["errors"] is None
    assert "1999-00" not in response.text


def test_list_matches_of_an_unknown_team_is_not_found(api_client: TestClient) -> None:
    response = api_client.get("/v1/matches", params={"team_id": 9999999})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Not Found"
    assert problem["status"] == 404
    assert problem["detail"] == "Team not found"
    assert problem["errors"] is None
    assert "9999999" not in response.text


def test_list_matches_of_a_team_without_matches_in_a_season_is_an_empty_page(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    gamma = create_team(db_session, name="Test FC Gamma")
    create_match(db_session, season=season, home=alpha, away=beta)

    response = api_client.get("/v1/matches", params={"season": season.code, "team_id": gamma.id})

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 100, "offset": 0}


@pytest.mark.parametrize(
    ("params", "location"),
    [
        ({"matchday": 0}, ["query", "matchday"]),
        ({"matchday": 47}, ["query", "matchday"]),
        ({"status": "postponed"}, ["query", "status"]),
        ({"order": "up"}, ["query", "order"]),
        ({"limit": 0}, ["query", "limit"]),
        ({"limit": 501}, ["query", "limit"]),
        ({"offset": -1}, ["query", "offset"]),
    ],
)
def test_list_matches_rejects_a_query_value_outside_its_rule(
    api_client: TestClient, params: dict[str, str | int], location: list[str]
) -> None:
    response = api_client.get("/v1/matches", params=params)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Unprocessable Content"
    assert problem["status"] == 422
    assert problem["detail"] == "Request validation failed"
    errors = problem["errors"]
    assert len(errors) == 1
    assert errors[0]["loc"] == location
    assert isinstance(errors[0]["msg"], str) and errors[0]["msg"]
    assert isinstance(errors[0]["type"], str) and errors[0]["type"]


def test_list_matches_rejects_a_date_range_that_ends_before_it_starts(
    api_client: TestClient,
) -> None:
    response = api_client.get(
        "/v1/matches",
        params={"date_from": "2098-09-01", "date_to": "2098-08-01"},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Unprocessable Content"
    assert problem["status"] == 422
    assert problem["detail"] == "Request validation failed"
    errors = problem["errors"]
    assert len(errors) == 1
    assert errors[0]["loc"] == ["query", "date_to"]
    assert "2098-09-01" not in response.text
    assert "2098-08-01" not in response.text


def test_get_a_played_match_returns_the_fixture_with_its_result(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    played = create_match(
        db_session,
        season=season,
        home=alpha,
        away=beta,
        kickoff_date=datetime.date(2098, 8, 15),
        kickoff_time=datetime.time(20, 0),
        status="played",
        home_goals=2,
        away_goals=1,
        ht_home_goals=1,
        ht_away_goals=0,
    )

    response = api_client.get(f"/v1/matches/{played.id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": played.id,
        "season": season.code,
        "kickoff_date": "2098-08-15",
        "kickoff_time": "20:00:00",
        "matchday": 1,
        "status": "played",
        "home_team": {"id": alpha.id, "name": "Test FC Alpha"},
        "away_team": {"id": beta.id, "name": "Test FC Beta"},
        "home_goals": 2,
        "away_goals": 1,
        "ht_home_goals": 1,
        "ht_away_goals": 0,
        "result": "H",
        "stats": None,
    }


def test_get_a_match_returns_its_statistics(api_client: TestClient, db_session: Session) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    played = create_match(
        db_session,
        season=season,
        home=alpha,
        away=beta,
        status="played",
        home_goals=2,
        away_goals=1,
    )
    db_session.add(
        MatchStat(
            match_id=played.id,
            home_shots=14,
            away_shots=9,
            home_shots_on_target=6,
            away_shots_on_target=3,
            home_fouls=11,
            away_fouls=13,
            home_corners=7,
            away_corners=2,
            home_yellow=1,
            away_yellow=3,
            home_red=0,
            away_red=None,
            home_xg=None,
            away_xg=decimal.Decimal("0.80"),
        )
    )
    db_session.flush()

    response = api_client.get(f"/v1/matches/{played.id}")

    assert response.status_code == 200
    assert response.json()["stats"] == {
        "home_shots": 14,
        "away_shots": 9,
        "home_shots_on_target": 6,
        "away_shots_on_target": 3,
        "home_fouls": 11,
        "away_fouls": 13,
        "home_corners": 7,
        "away_corners": 2,
        "home_yellow": 1,
        "away_yellow": 3,
        "home_red": 0,
        "away_red": None,
        "home_xg": None,
        "away_xg": 0.8,
    }


def test_get_a_match_without_statistics_returns_no_statistics_object(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    scheduled = create_match(db_session, season=season, home=alpha, away=beta)

    response = api_client.get(f"/v1/matches/{scheduled.id}")

    assert response.status_code == 200
    detail = response.json()
    assert detail["stats"] is None
    assert detail["status"] == "scheduled"
    assert detail["home_goals"] is None
    assert detail["result"] is None


def test_get_an_unknown_match_is_not_found(api_client: TestClient) -> None:
    response = api_client.get("/v1/matches/9999999")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Not Found"
    assert problem["status"] == 404
    assert problem["detail"] == "Match not found"
    assert problem["errors"] is None
    assert "9999999" not in response.text


def test_get_a_match_with_a_non_integer_id_is_rejected_without_echoing_it(
    api_client: TestClient,
) -> None:
    response = api_client.get("/v1/matches/not-a-match")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["detail"] == "Request validation failed"
    errors = problem["errors"]
    assert len(errors) == 1
    assert errors[0]["loc"] == ["path", "match_id"]
    assert "not-a-match" not in response.text


def test_list_matches_issues_two_statements_for_one_page(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    gamma = create_team(db_session, name="Test FC Gamma")
    pairs = [
        (alpha, beta),
        (beta, gamma),
        (gamma, alpha),
        (alpha, gamma),
        (beta, alpha),
        (gamma, beta),
    ]
    for home, away in pairs:
        create_match(db_session, season=season, home=home, away=away)

    statements: list[str] = []

    def record(*arguments: object) -> None:
        statements.append(str(arguments[2]))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        response = api_client.get("/v1/matches", params={"season": season.code, "limit": 5})
    finally:
        event.remove(bind, "before_cursor_execute", record)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 5
    assert len(statements) == 2


def test_openapi_documents_the_matches_contract() -> None:
    document = create_app().openapi()
    collection = document["paths"]["/v1/matches"]["get"]
    single = document["paths"]["/v1/matches/{match_id}"]["get"]

    assert collection["operationId"] == "list_matches"
    assert single["operationId"] == "get_match"

    page_schema = collection["responses"]["200"]["content"]["application/json"]["schema"]
    assert page_schema["$ref"] == MATCH_PAGE_REF
    detail_schema = single["responses"]["200"]["content"]["application/json"]["schema"]
    assert detail_schema["$ref"] == MATCH_DETAIL_REF

    schemas = document["components"]["schemas"]
    assert schemas["MatchPage"]["properties"]["items"]["items"]["$ref"] == MATCH_SUMMARY_REF

    for operation in (collection, single):
        for status in ("404", "422"):
            content = operation["responses"][status]["content"]
            assert PROBLEM_MEDIA_TYPE in content
            assert any(media.get("schema") == {"$ref": PROBLEM_REF} for media in content.values())

    assert "HTTPValidationError" not in schemas
