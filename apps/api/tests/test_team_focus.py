"""Contract tests for the ``/v1/teams/{team_id}/focus`` endpoint on a disposable database.

The suite shares its database with the seasons and the matches the real
ingestion left behind, so every test bounds its requests with the synthetic
season code it created and asserts on the rows it stored itself, never on the
absolute size of a table.

A stored prediction cannot be inserted freely: the ``predictions_guard_insert``
trigger accepts only a scheduled match dated after Madrid today and stamps
``created_at`` itself. Every fixture a forecast is stored for is therefore dated
a few days after :func:`tests.live_support.madrid_today`, which reads the Madrid
date of the database, and the season code is far in the future so no seeded or
ingested season can collide with it.

The expectations are computed by hand from the synthetic rows, which the docstring
of each builder tabulates: the letters, the points, the goals, the position and
the probabilities are all reachable with a pencil, which is what makes the
assertions evidence instead of a restatement of the code under test.
"""

import datetime
from typing import Any, NamedTuple

import pytest
from fastapi.testclient import TestClient
from lmi_api.main import create_app
from lmi_core.db.models import Match, Season, Team
from sqlalchemy.orm import Session

from tests.factories import create_match, create_season, create_team
from tests.live_support import create_prediction, create_prediction_run, madrid_today
from tests.team_focus_support import create_simulation_run, create_team_result

pytestmark = pytest.mark.integration

SEASON_CODE = "2098-99"
"""The synthetic season code of these tests: no data source covers 2098."""

SEASON_START_YEAR = 2098
"""The start year of :data:`SEASON_CODE`, which the season table keeps unique."""

OTHER_SEASON_CODE = "2097-98"
"""A second synthetic season, used to prove that a club of one is not of the other."""

FOCUS_PATH = "/v1/teams/{team_id}/focus"
"""The path under test, formatted with a team identifier."""

TEAM_FOCUS_REF = "#/components/schemas/TeamFocus"
PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type every error response is sent with."""

FORM_LENGTH = 5
"""How many played matches the form summary covers."""

UPCOMING_LENGTH = 5
"""How many fixtures the upcoming list serves."""

FORM_FIELDS = {"team", "results", "points", "goals_for", "goals_against", "matches"}
SPLIT_FIELDS = {
    "played",
    "won",
    "drawn",
    "lost",
    "goals_for",
    "goals_against",
    "points",
    "points_per_game",
}
BRIEF_FIELDS = {
    "prediction_id",
    "model",
    "model_version",
    "created_at",
    "as_of_date",
    "probabilities",
    "evaluation",
}
OUTLOOK_FIELDS = {
    "run_id",
    "as_of_date",
    "p_title",
    "p_ucl",
    "p_europe",
    "p_relegation",
    "expected_points",
    "points_p05",
    "points_p95",
    "expected_position",
}
"""The field sets of the nested payloads, so a silent addition is visible."""

COUNTED_STANDING_FIELDS = (
    ("goals_for", 11),
    ("goals_against", 6),
    ("goal_difference", 5),
    ("points", 13),
)
"""The table counters of the focus club, hand-computed from the six results below."""


class World(NamedTuple):
    """One synthetic season, with its rows in the order the assertions read them.

    ``played`` is oldest first, ``upcoming`` is the five fixtures the endpoint
    serves in kickoff order, ``unrelated`` is a fixture of the same season that
    another club plays and that the team focus must therefore not list, and
    ``brief_ids`` maps a fixture identifier to the identifiers of its stored
    forecasts in the order they are served.
    """

    season: Season
    focus: Team
    played: list[Match]
    past_fixture: Match
    unrelated: Match
    upcoming: list[Match]
    beyond: Match
    brief_ids: dict[int, list[int]]


def test_the_focus_payload_of_a_team_with_played_and_scheduled_matches(
    api_client: TestClient, db_session: Session
) -> None:
    world = _build_world(db_session)

    response = _focus(api_client, world.focus.id, SEASON_CODE)

    assert set(response) == {
        "team",
        "season",
        "standing",
        "form",
        "splits",
        "upcoming",
        "outlook",
    }
    assert response["team"] == {"id": world.focus.id, "name": "Synthetic Focus FC"}
    assert response["season"] == SEASON_CODE

    standing = response["standing"]
    assert standing["team"] == {"id": world.focus.id, "name": "Synthetic Focus FC"}
    assert standing["position"] == 1
    assert (standing["played"], standing["won"], standing["drawn"], standing["lost"]) == (
        6,
        4,
        1,
        1,
    )
    for field, expected in COUNTED_STANDING_FIELDS:
        assert standing[field] == expected
    assert standing["form"] == ["W", "W", "W", "L", "D"]

    form = response["form"]
    assert set(form) == FORM_FIELDS
    assert form["team"] == {"id": world.focus.id, "name": "Synthetic Focus FC"}
    assert form["results"] == ["W", "W", "W", "L", "D"]
    assert (form["points"], form["goals_for"], form["goals_against"]) == (10, 9, 6)
    expected_matches = list(reversed(world.played))[:FORM_LENGTH]
    assert [match["id"] for match in form["matches"]] == [match.id for match in expected_matches]
    assert [match["kickoff_date"] for match in form["matches"]] == sorted(
        (match["kickoff_date"] for match in form["matches"]), reverse=True
    )
    assert form["matches"][0]["status"] == "played"
    assert world.played[0].id not in [match["id"] for match in form["matches"]]

    home = response["splits"]["home"]
    away = response["splits"]["away"]
    assert set(home) == SPLIT_FIELDS
    assert set(away) == SPLIT_FIELDS
    assert (home["played"], home["won"], home["drawn"], home["lost"]) == (3, 2, 0, 1)
    assert (home["goals_for"], home["goals_against"], home["points"]) == (6, 4, 6)
    assert home["points_per_game"] == pytest.approx(2.0)
    assert (away["played"], away["won"], away["drawn"], away["lost"]) == (3, 2, 1, 0)
    assert (away["goals_for"], away["goals_against"], away["points"]) == (5, 2, 7)
    assert away["points_per_game"] == pytest.approx(7 / 3)

    # The two venue records partition the season's played matches, so their sum
    # is the table row the ranking decided. The check ties the arithmetic of
    # :func:`lmi_api.views.split_record` to
    # :func:`lmi_core.standings.compute_standings`: if the core points rule or
    # the win/draw/loss rule changed, the served splits would no longer add up
    # to the standing and this assertion would fail.
    for field in ("played", "won", "drawn", "lost", "goals_for", "goals_against", "points"):
        assert home[field] + away[field] == standing[field], field

    assert response["outlook"] is None
    _assert_upcoming(api_client, world, response["upcoming"])


def test_the_outlook_is_the_team_row_of_the_newest_succeeded_run(
    api_client: TestClient, db_session: Session
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    focus = create_team(db_session, name="Synthetic Focus FC")
    opponent = create_team(db_session, name="Synthetic Opponent FC")
    create_match(
        db_session,
        season=season,
        home=focus,
        away=opponent,
        kickoff_date=today + _days(2),
    )

    oldest = create_simulation_run(
        db_session,
        season=season,
        as_of_date=today - _days(10),
        finished_at=_instant(today - _days(9)),
    )
    create_team_result(db_session, run=oldest, team=focus, mean_points=30.0, p_title=0.9)
    newer_as_of = create_simulation_run(
        db_session,
        season=season,
        as_of_date=today - _days(2),
        finished_at=_instant(today - _days(4)),
    )
    create_team_result(db_session, run=newer_as_of, team=focus, mean_points=41.0, p_title=0.8)
    # The served run: the latest finish of the season, although a succeeded run
    # with a newer as-of date exists, so the finished_at order is pinned.
    latest = create_simulation_run(
        db_session,
        season=season,
        as_of_date=today - _days(5),
        finished_at=_instant(today - _days(1)),
    )
    create_team_result(
        db_session,
        run=latest,
        team=focus,
        mean_points=71.5,
        mean_position=2.75,
        points_p05=61,
        points_p95=83,
        p_title=0.25,
        p_ucl=0.6,
        p_europe=0.75,
        p_relegation=0.02,
    )
    failed = create_simulation_run(
        db_session,
        season=season,
        status="failed",
        as_of_date=today,
        finished_at=_instant(today),
    )
    create_team_result(db_session, run=failed, team=focus, mean_points=1.0, p_title=1.0)
    running = create_simulation_run(db_session, season=season, status="running", as_of_date=today)
    create_team_result(db_session, run=running, team=focus, mean_points=2.0, p_title=1.0)

    response = _focus(api_client, focus.id, season.code)

    outlook = response["outlook"]
    assert set(outlook) == OUTLOOK_FIELDS
    assert outlook["run_id"] == str(latest.id)
    assert outlook["as_of_date"] == latest.as_of_date.isoformat()
    assert outlook["p_title"] == pytest.approx(0.25)
    assert outlook["p_ucl"] == pytest.approx(0.6)
    assert outlook["p_europe"] == pytest.approx(0.75)
    assert outlook["p_relegation"] == pytest.approx(0.02)
    assert outlook["expected_points"] == pytest.approx(71.5)
    assert outlook["expected_position"] == pytest.approx(2.75)
    assert outlook["points_p05"] == 61
    assert outlook["points_p95"] == 83


def test_a_team_that_appears_in_the_season_but_played_nothing(
    api_client: TestClient, db_session: Session
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    focus = create_team(db_session, name="Synthetic Idle FC")
    opponent = create_team(db_session, name="Synthetic Opponent FC")
    # A withdrawn fixture keeps the club in the season without a result and
    # without a fixture still to be played.
    create_match(
        db_session,
        season=season,
        home=focus,
        away=opponent,
        kickoff_date=today + _days(4),
        status="withdrawn",
        withdrawn_at=datetime.datetime.now(datetime.UTC),
    )

    response = _focus(api_client, focus.id, season.code)

    standing = response["standing"]
    assert standing is not None
    assert standing["position"] == 1
    assert (standing["played"], standing["points"], standing["form"]) == (0, 0, [])
    form = response["form"]
    assert form["results"] == []
    assert form["matches"] == []
    assert (form["points"], form["goals_for"], form["goals_against"]) == (0, 0, 0)
    assert response["splits"]["home"] == {
        "played": 0,
        "won": 0,
        "drawn": 0,
        "lost": 0,
        "goals_for": 0,
        "goals_against": 0,
        "points": 0,
        "points_per_game": None,
    }
    assert response["splits"]["away"] == response["splits"]["home"]
    assert response["upcoming"] == []
    assert response["outlook"] is None


def test_an_unknown_team_an_unknown_season_and_a_team_of_another_season_are_not_found(
    api_client: TestClient, db_session: Session
) -> None:
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    other = create_season(db_session, code=OTHER_SEASON_CODE, start_year=SEASON_START_YEAR - 1)
    focus = create_team(db_session, name="Synthetic Focus FC")
    opponent = create_team(db_session, name="Synthetic Opponent FC")
    create_match(db_session, season=other, home=focus, away=opponent, kickoff_date=today + _days(3))
    unknown = 2_147_483_647

    unknown_team = api_client.get(FOCUS_PATH.format(team_id=unknown))
    unknown_season = api_client.get(
        FOCUS_PATH.format(team_id=focus.id), params={"season": "1999-00"}
    )
    other_season = api_client.get(
        FOCUS_PATH.format(team_id=focus.id), params={"season": season.code}
    )

    for response, detail in (
        (unknown_team, f"Team {unknown} not found"),
        (unknown_season, "Season not found"),
        (other_season, "Team not found in the season"),
    ):
        assert response.status_code == 404
        assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
        problem = response.json()
        assert problem["title"] == "Not Found"
        assert problem["status"] == 404
        assert problem["detail"] == detail
        assert problem["errors"] is None
    assert str(focus.id) not in other_season.text
    assert season.code not in other_season.text


def test_a_non_integer_team_id_is_unprocessable_without_echoing_the_value(
    api_client: TestClient,
) -> None:
    response = api_client.get("/v1/teams/not-a-team/focus")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Unprocessable Content"
    assert problem["status"] == 422
    assert problem["detail"] == "Request validation failed"
    errors = problem["errors"]
    assert errors
    assert ["path", "team_id"] in [error["loc"] for error in errors]
    assert "not-a-team" not in response.text


def test_openapi_documents_the_team_focus_contract() -> None:
    document = create_app().openapi()
    operation = document["paths"]["/v1/teams/{team_id}/focus"]["get"]

    assert operation["operationId"] == "get_team_focus"
    # The nested resource shares the tag of the team resource it describes, so a
    # generated client sees one group per entity and not one per path shape.
    assert operation["tags"] == ["teams"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": TEAM_FOCUS_REF
    }
    for status in ("404", "422"):
        content = operation["responses"][status]["content"]
        assert PROBLEM_MEDIA_TYPE in content
        assert any(media.get("schema") == {"$ref": PROBLEM_REF} for media in content.values())

    assert document["paths"]["/v1/teams/{team_id}"]["get"]["operationId"] == "get_team"
    assert document["paths"]["/v1/teams/{team_id}"]["get"]["tags"] == ["teams"]
    assert "HTTPValidationError" not in document["components"]["schemas"]


def _focus(api_client: TestClient, team_id: int, season: str) -> dict[str, Any]:
    """Return the payload of one successful focus request."""
    response = api_client.get(FOCUS_PATH.format(team_id=team_id), params={"season": season})
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _assert_upcoming(api_client: TestClient, world: World, upcoming: list[dict[str, Any]]) -> None:
    """Assert the upcoming list of ``world`` and that its briefs are followable.

    The served fixtures are the five next ones of the focus club in kickoff
    order: the fixture dated before Madrid today, the sixth fixture and another
    club's fixture of the same season are all absent. The first fixture carries
    two forecasts, the second one, and the rest none, so the read is not one
    query per fixture.
    """
    assert [item["match"]["id"] for item in upcoming] == [match.id for match in world.upcoming]
    assert len(upcoming) == UPCOMING_LENGTH
    assert world.past_fixture.id not in [item["match"]["id"] for item in upcoming]
    assert world.unrelated.id not in [item["match"]["id"] for item in upcoming]
    assert world.beyond.id not in [item["match"]["id"] for item in upcoming]
    assert [item["match"]["kickoff_date"] for item in upcoming] == sorted(
        item["match"]["kickoff_date"] for item in upcoming
    )
    assert [item["match"]["status"] for item in upcoming] == ["scheduled"] * len(upcoming)
    assert [len(item["predictions"]) for item in upcoming] == [2, 1, 0, 0, 0]

    served = upcoming[0]
    assert served["match"]["home_team"]["id"] == world.upcoming[0].home_team_id
    assert served["match"]["away_team"]["id"] == world.focus.id
    briefs = served["predictions"]
    assert set(briefs[0]) == BRIEF_FIELDS
    assert [brief["model"] for brief in briefs] == ["home_win", "dixon_coles"]
    assert [brief["prediction_id"] for brief in briefs] == world.brief_ids[served["match"]["id"]]
    assert [brief["evaluation"] for brief in briefs] == ["pending", "pending"]
    assert briefs[0]["probabilities"] == {"home": 0.5, "draw": 0.3, "away": 0.2}
    assert briefs[1]["probabilities"] == {"home": 0.4, "draw": 0.3, "away": 0.3}

    for brief in briefs:
        detail = api_client.get(f"/v1/predictions/{brief['prediction_id']}")
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["id"] == brief["prediction_id"]
        assert payload["model"] == brief["model"]
        assert payload["match"]["id"] == served["match"]["id"]


def _build_world(session: Session) -> World:
    """Store one synthetic season and return it in the order the assertions read it.

    Six played matches of the focus club, one fixture dated before Madrid today
    and six dated after it. The results, newest first, read ``W W W L D W``, so
    the form covers the last five of them and the table of the season carries
    13 points, 11 goals for and 6 against.
    """
    today = madrid_today(session)
    season = create_season(session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    focus = create_team(session, name="Synthetic Focus FC")
    beta = create_team(session, name="Synthetic Beta FC")
    gamma = create_team(session, name="Synthetic Gamma FC")
    delta = create_team(session, name="Synthetic Delta FC")
    epsilon = create_team(session, name="Synthetic Epsilon FC")
    zeta = create_team(session, name="Synthetic Zeta FC")
    eta = create_team(session, name="Synthetic Eta FC")
    theta = create_team(session, name="Synthetic Theta FC")

    played = [
        _played(session, season, home=focus, away=beta, home_goals=2, away_goals=0, day=10),
        _played(session, season, home=gamma, away=focus, home_goals=1, away_goals=1, day=17),
        _played(session, season, home=focus, away=gamma, home_goals=1, away_goals=3, day=24),
        _played(session, season, home=delta, away=focus, home_goals=0, away_goals=2, day=31),
        _played(session, season, home=focus, away=delta, home_goals=3, away_goals=1, day=38),
        _played(session, season, home=beta, away=focus, home_goals=1, away_goals=2, day=45),
    ]

    past_fixture = _fixture(session, season, focus, epsilon, today - _days(3), matchday=7)
    # A fixture of the same season that the focus club does not play, dated
    # before every fixture of the list: a season-wide upcoming list would serve
    # it instead of the fifth fixture and the assertions below would fail.
    unrelated = _fixture(session, season, epsilon, theta, today + _days(1), matchday=7)
    first = _fixture(session, season, epsilon, focus, today + _days(2), matchday=8)
    second = _fixture(session, season, focus, zeta, today + _days(3), matchday=9)
    third = _fixture(session, season, zeta, focus, today + _days(4), matchday=10)
    fourth = _fixture(session, season, focus, eta, today + _days(4), matchday=10)
    fifth = _fixture(session, season, eta, focus, today + _days(5), matchday=11)
    beyond = _fixture(session, season, focus, theta, today + _days(6), matchday=12)

    run = create_prediction_run(session)
    first_home_win = create_prediction(
        session, match=first, model="home_win", run=run, probabilities=(0.5, 0.3, 0.2)
    )
    first_dixon_coles = create_prediction(
        session, match=first, model="dixon_coles", run=run, probabilities=(0.4, 0.3, 0.3)
    )
    second_home_win = create_prediction(session, match=second, model="home_win", run=run)
    return World(
        season=season,
        focus=focus,
        played=played,
        past_fixture=past_fixture,
        unrelated=unrelated,
        upcoming=[first, second, third, fourth, fifth],
        beyond=beyond,
        brief_ids={
            first.id: [first_home_win.id, first_dixon_coles.id],
            second.id: [second_home_win.id],
        },
    )


def _fixture(
    session: Session,
    season: Season,
    home: Team,
    away: Team,
    kickoff_date: datetime.date,
    *,
    matchday: int,
) -> Match:
    """Store one scheduled fixture of the synthetic season."""
    return create_match(
        session,
        season=season,
        home=home,
        away=away,
        kickoff_date=kickoff_date,
        matchday=matchday,
    )


def _played(
    session: Session,
    season: Season,
    *,
    home: Team,
    away: Team,
    home_goals: int,
    away_goals: int,
    day: int,
) -> Match:
    """Store one played match of 2098, on the given day counted from 1 August."""
    kickoff_date = datetime.date(2098, 8, 1) + _days(day - 1)
    return create_match(
        session,
        season=season,
        home=home,
        away=away,
        kickoff_date=kickoff_date,
        status="played",
        home_goals=home_goals,
        away_goals=away_goals,
    )


def _days(count: int) -> datetime.timedelta:
    """Return ``count`` whole days as a timedelta."""
    return datetime.timedelta(days=count)


def _instant(day: datetime.date) -> datetime.datetime:
    """Return the UTC instant of ten o'clock on ``day``.

    The hour is invented: nothing in the schema orders runs by the time of day,
    and a fixed one keeps the assertions deterministic.
    """
    return datetime.datetime.combine(day, datetime.time(10, 0), tzinfo=datetime.UTC)
