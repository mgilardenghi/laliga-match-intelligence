"""Contract tests for the ``/v1/standings`` endpoint against a disposable database.

The suite shares its database with the seeded seasons and the ingested matches,
so every test that omits the ``season`` parameter relies on the default
selection and bounds itself: one names a synthetic season in the far future, the
other removes every fixture first, inside the transaction the fixture rolls
back. Every other test names the code of the season it inserted.

The ranking itself is exercised by the core suite; what these tests pin is the
seam: which season is selected, which teams make up the table, which results are
counted, and the payload the client parses.
"""

import datetime

import pytest
from fastapi.testclient import TestClient
from lmi_api.main import create_app
from lmi_core.db.models import Match
from sqlalchemy import delete, event
from sqlalchemy.orm import Session

from tests.factories import create_match, create_season, create_team

pytestmark = pytest.mark.integration

STANDINGS_REF = "#/components/schemas/Standings"
PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type every error response is sent with."""


def test_standings_before_the_first_played_match_list_every_team_with_zeros(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    gamma = create_team(db_session, name="Test FC Gamma")
    delta = create_team(db_session, name="Test FC Delta")
    create_match(db_session, season=season, home=alpha, away=beta, matchday=1)
    create_match(db_session, season=season, home=gamma, away=delta, matchday=1)
    create_match(db_session, season=season, home=beta, away=gamma, matchday=2)

    response = api_client.get("/v1/standings", params={"season": season.code})

    assert response.status_code == 200
    table = response.json()
    assert table["matches_counted"] == 0
    assert [row["team"]["name"] for row in table["rows"]] == [
        "Test FC Alpha",
        "Test FC Beta",
        "Test FC Delta",
        "Test FC Gamma",
    ]
    assert [row["position"] for row in table["rows"]] == [1, 2, 3, 4]
    for row in table["rows"]:
        assert row["played"] == 0
        assert row["won"] == 0
        assert row["drawn"] == 0
        assert row["lost"] == 0
        assert row["goals_for"] == 0
        assert row["goals_against"] == 0
        assert row["goal_difference"] == 0
        assert row["points"] == 0
        assert row["form"] == []


def test_standings_count_every_played_match_and_rank_the_hand_computed_table(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    gamma = create_team(db_session, name="Test FC Gamma")
    create_match(
        db_session,
        season=season,
        home=alpha,
        away=beta,
        kickoff_date=datetime.date(2098, 8, 15),
        matchday=1,
        status="played",
        home_goals=2,
        away_goals=0,
    )
    create_match(
        db_session,
        season=season,
        home=beta,
        away=gamma,
        kickoff_date=datetime.date(2098, 8, 22),
        matchday=2,
        status="played",
        home_goals=1,
        away_goals=1,
    )
    create_match(
        db_session,
        season=season,
        home=gamma,
        away=alpha,
        kickoff_date=datetime.date(2098, 8, 29),
        matchday=3,
        status="played",
        home_goals=0,
        away_goals=3,
    )

    response = api_client.get("/v1/standings", params={"season": season.code})

    assert response.status_code == 200
    table = response.json()
    assert table["season"] == season.code
    assert table["as_of"] is None
    assert table["matches_counted"] == 3
    assert table["rows"] == [
        {
            "position": 1,
            "team": {"id": alpha.id, "name": "Test FC Alpha"},
            "played": 2,
            "won": 2,
            "drawn": 0,
            "lost": 0,
            "goals_for": 5,
            "goals_against": 0,
            "goal_difference": 5,
            "points": 6,
            "form": ["W", "W"],
        },
        {
            "position": 2,
            "team": {"id": beta.id, "name": "Test FC Beta"},
            "played": 2,
            "won": 0,
            "drawn": 1,
            "lost": 1,
            "goals_for": 1,
            "goals_against": 3,
            "goal_difference": -2,
            "points": 1,
            "form": ["D", "L"],
        },
        {
            "position": 3,
            "team": {"id": gamma.id, "name": "Test FC Gamma"},
            "played": 2,
            "won": 0,
            "drawn": 1,
            "lost": 1,
            "goals_for": 1,
            "goals_against": 4,
            "goal_difference": -3,
            "points": 1,
            "form": ["L", "D"],
        },
    ]


def test_standings_as_of_includes_the_day_it_names(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    gamma = create_team(db_session, name="Test FC Gamma")
    delta = create_team(db_session, name="Test FC Delta")
    create_match(
        db_session,
        season=season,
        home=alpha,
        away=beta,
        kickoff_date=datetime.date(2098, 8, 15),
        matchday=1,
        status="played",
        home_goals=1,
        away_goals=0,
    )
    create_match(
        db_session,
        season=season,
        home=gamma,
        away=delta,
        kickoff_date=datetime.date(2098, 8, 15),
        matchday=1,
        status="played",
        home_goals=2,
        away_goals=2,
    )
    create_match(
        db_session,
        season=season,
        home=beta,
        away=gamma,
        kickoff_date=datetime.date(2098, 8, 22),
        matchday=2,
        status="played",
        home_goals=3,
        away_goals=0,
    )
    create_match(
        db_session,
        season=season,
        home=delta,
        away=alpha,
        kickoff_date=datetime.date(2098, 8, 22),
        matchday=2,
        status="played",
        home_goals=0,
        away_goals=1,
    )

    before_second_matchday = api_client.get(
        "/v1/standings", params={"season": season.code, "as_of": "2098-08-21"}
    )
    on_second_matchday = api_client.get(
        "/v1/standings", params={"season": season.code, "as_of": "2098-08-22"}
    )

    assert before_second_matchday.status_code == 200
    assert before_second_matchday.json()["matches_counted"] == 2
    assert before_second_matchday.json()["as_of"] == "2098-08-21"
    assert on_second_matchday.status_code == 200
    assert on_second_matchday.json()["matches_counted"] == 4
    assert on_second_matchday.json()["as_of"] == "2098-08-22"

    early_alpha = _row_of(before_second_matchday.json(), alpha.id)
    late_alpha = _row_of(on_second_matchday.json(), alpha.id)
    assert early_alpha["played"] == 1
    assert early_alpha["points"] == 3
    assert late_alpha["played"] == 2
    assert late_alpha["points"] == 6


def test_standings_ignore_a_scheduled_match_with_a_past_date(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    gamma = create_team(db_session, name="Test FC Gamma")
    delta = create_team(db_session, name="Test FC Delta")
    create_match(
        db_session,
        season=season,
        home=alpha,
        away=beta,
        kickoff_date=datetime.date(2020, 1, 5),
        matchday=1,
        status="scheduled",
    )
    create_match(
        db_session,
        season=season,
        home=gamma,
        away=delta,
        kickoff_date=datetime.date(2020, 1, 5),
        matchday=1,
        status="played",
        home_goals=2,
        away_goals=1,
    )

    response = api_client.get("/v1/standings", params={"season": season.code})

    assert response.status_code == 200
    table = response.json()
    assert table["matches_counted"] == 1
    postponed = _row_of(table, alpha.id)
    assert postponed["played"] == 0
    assert postponed["points"] == 0
    assert postponed["form"] == []
    assert _row_of(table, beta.id)["played"] == 0
    winner = _row_of(table, gamma.id)
    assert winner["played"] == 1
    assert winner["points"] == 3


def test_standings_of_a_season_without_matches_are_empty(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    create_team(db_session, name="Test FC Alpha")

    response = api_client.get("/v1/standings", params={"season": season.code})

    assert response.status_code == 200
    assert response.json() == {
        "season": season.code,
        "as_of": None,
        "matches_counted": 0,
        "rows": [],
    }


def test_standings_without_a_season_and_without_any_match_are_not_found(
    api_client: TestClient, db_session: Session
) -> None:
    """Pin the 404 the default season falls back to when nothing was ingested.

    Every fixture is removed first, inside the outer transaction the fixture
    rolls back, so the assertion holds whatever the database held before the
    test; ``match_stats`` follows on the ``ON DELETE CASCADE`` of its foreign
    key.
    """
    db_session.execute(delete(Match))

    response = api_client.get("/v1/standings")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "No season with matches",
        "errors": None,
    }


def test_standings_of_an_unknown_season_are_not_found_without_echoing_it(
    api_client: TestClient,
) -> None:
    response = api_client.get("/v1/standings", params={"season": "2097-98"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Not Found"
    assert problem["status"] == 404
    assert problem["detail"] == "Season not found"
    assert problem["errors"] is None
    assert "2097-98" not in response.text


def test_standings_with_an_invalid_as_of_are_rejected_without_echoing_it(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)

    response = api_client.get(
        "/v1/standings", params={"season": season.code, "as_of": "not-a-date"}
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Unprocessable Content"
    assert problem["status"] == 422
    assert problem["detail"] == "Request validation failed"
    errors = problem["errors"]
    assert len(errors) == 1
    assert errors[0]["loc"] == ["query", "as_of"]
    assert "not-a-date" not in response.text


def test_standings_default_to_the_season_that_starts_latest_and_has_matches(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    create_match(
        db_session,
        season=season,
        home=alpha,
        away=beta,
        kickoff_date=datetime.date(2098, 8, 15),
        matchday=1,
        status="played",
        home_goals=1,
        away_goals=1,
    )

    response = api_client.get("/v1/standings")

    assert response.status_code == 200
    table = response.json()
    assert table["season"] == season.code
    assert table["as_of"] is None
    assert table["matches_counted"] == 1
    assert [row["team"]["name"] for row in table["rows"]] == ["Test FC Alpha", "Test FC Beta"]


def test_standings_issue_three_statements_whatever_the_table_size(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session)
    alpha = create_team(db_session, name="Test FC Alpha")
    beta = create_team(db_session, name="Test FC Beta")
    gamma = create_team(db_session, name="Test FC Gamma")
    create_match(
        db_session,
        season=season,
        home=alpha,
        away=beta,
        kickoff_date=datetime.date(2098, 8, 15),
        matchday=1,
        status="played",
        home_goals=2,
        away_goals=1,
    )
    create_match(
        db_session,
        season=season,
        home=beta,
        away=gamma,
        kickoff_date=datetime.date(2098, 8, 22),
        matchday=2,
        status="played",
        home_goals=0,
        away_goals=0,
    )

    statements: list[str] = []

    def record(*arguments: object) -> None:
        statements.append(str(arguments[2]))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        response = api_client.get("/v1/standings", params={"season": season.code})
    finally:
        event.remove(bind, "before_cursor_execute", record)

    assert response.status_code == 200
    assert len(response.json()["rows"]) == 3
    assert len(statements) == 3


def test_openapi_documents_the_standings_contract() -> None:
    document = create_app().openapi()
    operation = document["paths"]["/v1/standings"]["get"]

    assert operation["operationId"] == "get_standings"

    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["$ref"] == STANDINGS_REF

    for status in ("404", "422"):
        content = operation["responses"][status]["content"]
        assert PROBLEM_MEDIA_TYPE in content
        assert any(media.get("schema") == {"$ref": PROBLEM_REF} for media in content.values())

    assert "HTTPValidationError" not in document["components"]["schemas"]


def _row_of(table: dict[str, object], team_id: int) -> dict[str, object]:
    """Return the row of ``team_id`` from a parsed standings payload.

    Raises:
        AssertionError: no row of the table names ``team_id``, which is a
            failure of the test's own expectation rather than of the payload.
    """
    rows = table["rows"]
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, dict)
        team = row["team"]
        assert isinstance(team, dict)
        if team["id"] == team_id:
            return row
    raise AssertionError(f"no standings row for team {team_id}")
