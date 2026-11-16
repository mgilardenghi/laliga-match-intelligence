"""Contract tests for ``GET /v1/matches/{match_id}/preview`` on a disposable database.

The suite shares its database with the seasons and the matches the real
ingestion left behind, so every test creates its own clubs, asserts on the rows
it stored itself and never on the absolute size of a table.

A stored prediction cannot be inserted freely: the ``predictions_guard_insert``
trigger accepts only a scheduled match dated after Madrid today and stamps
``created_at`` itself. A fixture that carries a forecast is therefore dated a
few days after :func:`tests.live_support.madrid_today`, which reads the Madrid
date of the database and never the Python clock, while every result behind a
form or a head-to-head record is dated earlier than the fixture on purpose: the
preview reads strictly before the fixture's own kickoff date.

The expectations are computed by hand from the synthetic rows, and each builder
tabulates them in its docstring: the letters, the points, the goals, the
counters and the probabilities are all reachable with a pencil. The
margin-removed probabilities come from
:func:`tests.match_preview_support.implied_probabilities`, which spells the
arithmetic out, rather than from the core function the endpoint calls, so the
assertion compares the payload against the definition and not against itself.
"""

import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from lmi_api.main import create_app
from lmi_core.db.models import Match, Season, Team
from sqlalchemy.orm import Session

from tests.factories import create_match, create_season, create_team
from tests.live_metrics_support import store_odds
from tests.live_support import (
    create_prediction,
    create_prediction_run,
    madrid_today,
    mark_played,
    mark_withdrawn,
)
from tests.match_preview_support import implied_probabilities

pytestmark = pytest.mark.integration

SEASON_CODE = "2098-99"
"""The synthetic season code of these tests: no data source covers 2098."""

SEASON_START_YEAR = 2098
"""The start year of :data:`SEASON_CODE`, which the season table keeps unique."""

OTHER_SEASON_CODE = "2097-98"
OTHER_SEASON_START_YEAR = 2097
"""A second synthetic season, so one pair can meet more than twice.

The schema keeps ``(season, home, away)`` unique, so a pair meets at most
once per season and venue: a test that needs a third meeting stores it in the
other season, which is exactly what a real league calendar does.
"""

PREVIEW_PATH = "/v1/matches/{match_id}/preview"
"""The path under test, formatted with a match identifier."""

MATCH_PREVIEW_REF = "#/components/schemas/MatchPreview"
PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type every error response is sent with."""

PREVIEW_FIELDS = {"match", "home_form", "away_form", "head_to_head", "predictions", "odds"}
FORM_FIELDS = {"team", "results", "points", "goals_for", "goals_against", "matches"}
HEAD_TO_HEAD_FIELDS = {"played", "home_side_wins", "draws", "away_side_wins", "matches"}
ODDS_FIELDS = {"bookmaker", "is_closing", "home", "draw", "away", "implied"}
BRIEF_FIELDS = {
    "prediction_id",
    "model",
    "model_version",
    "created_at",
    "as_of_date",
    "probabilities",
    "evaluation",
}
"""The field sets of the payload and its nested objects, so a silent addition is visible."""


def test_the_preview_of_a_scheduled_fixture_between_two_clubs_that_have_met(
    api_client: TestClient, db_session: Session
) -> None:
    """The stored fixture, both forms, the record of the pair and the forecasts."""
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    earlier_season = create_season(
        db_session, code=OTHER_SEASON_CODE, start_year=OTHER_SEASON_START_YEAR
    )
    home = create_team(db_session, name="Synthetic Preview Home FC")
    away = create_team(db_session, name="Synthetic Preview Away FC")
    other = create_team(db_session, name="Synthetic Preview Other FC")

    # The form of the home side, newest first, reads W D W W: the third meeting
    # below is dated last and came from the fixture's away side.
    oldest = _played(
        db_session,
        season,
        home=home,
        away=other,
        days_ago=55,
        home_goals=1,
        away_goals=0,
    )
    # The pair meets at both venues, once in each season, so the fixture's own
    # season has the one home-side meeting left for the fixture itself.
    first_meeting = _played(
        db_session,
        season,
        home=away,
        away=home,
        days_ago=40,
        home_goals=1,
        away_goals=3,
    )
    second_meeting = _played(
        db_session,
        earlier_season,
        home=home,
        away=away,
        days_ago=25,
        home_goals=0,
        away_goals=0,
    )
    third_meeting = _played(
        db_session,
        earlier_season,
        home=away,
        away=home,
        days_ago=10,
        home_goals=1,
        away_goals=3,
    )
    fixture = _fixture(
        db_session, season, home=home, away=away, kickoff_date=today + _days(3), matchday=10
    )
    run = create_prediction_run(db_session)
    home_win = create_prediction(
        db_session, match=fixture, model="home_win", run=run, probabilities=(0.5, 0.3, 0.2)
    )
    dixon_coles = create_prediction(
        db_session, match=fixture, model="dixon_coles", run=run, probabilities=(0.4, 0.35, 0.25)
    )

    payload = _preview(api_client, fixture.id)

    assert set(payload) == PREVIEW_FIELDS
    assert payload["match"]["id"] == fixture.id
    assert payload["match"]["season"] == SEASON_CODE
    assert payload["match"]["status"] == "scheduled"
    assert payload["match"]["kickoff_date"] == fixture.kickoff_date.isoformat()
    assert payload["match"]["home_team"] == {"id": home.id, "name": "Synthetic Preview Home FC"}
    assert payload["match"]["away_team"] == {"id": away.id, "name": "Synthetic Preview Away FC"}
    assert (payload["match"]["home_goals"], payload["match"]["result"]) == (None, None)

    home_form = payload["home_form"]
    assert set(home_form) == FORM_FIELDS
    assert home_form["team"] == {"id": home.id, "name": "Synthetic Preview Home FC"}
    assert home_form["results"] == ["W", "D", "W", "W"]
    assert (home_form["points"], home_form["goals_for"], home_form["goals_against"]) == (10, 7, 2)
    assert [match["id"] for match in home_form["matches"]] == [
        third_meeting.id,
        second_meeting.id,
        first_meeting.id,
        oldest.id,
    ]

    away_form = payload["away_form"]
    assert set(away_form) == FORM_FIELDS
    assert away_form["team"] == {"id": away.id, "name": "Synthetic Preview Away FC"}
    assert away_form["results"] == ["L", "D", "L"]
    assert (away_form["points"], away_form["goals_for"], away_form["goals_against"]) == (1, 2, 6)
    assert [match["id"] for match in away_form["matches"]] == [
        third_meeting.id,
        second_meeting.id,
        first_meeting.id,
    ]

    head_to_head = payload["head_to_head"]
    assert set(head_to_head) == HEAD_TO_HEAD_FIELDS
    assert head_to_head["played"] == 3
    assert head_to_head["home_side_wins"] == 2
    assert head_to_head["draws"] == 1
    assert head_to_head["away_side_wins"] == 0
    assert [match["id"] for match in head_to_head["matches"]] == [
        third_meeting.id,
        second_meeting.id,
        first_meeting.id,
    ]
    assert [match["status"] for match in head_to_head["matches"]] == ["played"] * 3

    assert [brief["prediction_id"] for brief in payload["predictions"]] == [
        home_win.id,
        dixon_coles.id,
    ]
    assert set(payload["predictions"][0]) == BRIEF_FIELDS
    assert [brief["model"] for brief in payload["predictions"]] == ["home_win", "dixon_coles"]
    assert [brief["evaluation"] for brief in payload["predictions"]] == ["pending", "pending"]
    assert payload["predictions"][0]["probabilities"] == {"home": 0.5, "draw": 0.3, "away": 0.2}
    assert payload["predictions"][1]["probabilities"] == {
        "home": 0.4,
        "draw": 0.35,
        "away": 0.25,
    }

    # No price was stored for the fixture, which is the normal state of a match
    # still to be played: no odds object, never a made-up one.
    assert payload["odds"] is None


def test_the_served_price_is_the_pre_closing_one_of_the_most_trusted_bookmaker(
    api_client: TestClient, db_session: Session
) -> None:
    """Priority decides the row, a closing price is never served and the margin is removed."""
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    home = create_team(db_session, name="Synthetic Odds Home FC")
    away = create_team(db_session, name="Synthetic Odds Away FC")
    fixture = _fixture(
        db_session, season, home=home, away=away, kickoff_date=today + _days(4), matchday=11
    )
    # A lower-priority quote is stored first, so a served row that followed the
    # insertion order instead of the priority would be this one.
    store_odds(db_session, match=fixture, bookmaker="bet365", home=1.9, draw=3.6, away=4.2)
    store_odds(db_session, match=fixture, bookmaker="market_avg", home=2.1, draw=3.4, away=3.9)
    # A closing price is only known at kickoff, so it is never eligible, even
    # from the most trusted bookmaker.
    store_odds(
        db_session,
        match=fixture,
        bookmaker="pinnacle",
        home=8.0,
        draw=8.0,
        away=8.0,
        is_closing=True,
    )
    store_odds(db_session, match=fixture, bookmaker="pinnacle", home=2.0, draw=3.5, away=4.0)

    payload = _preview(api_client, fixture.id)

    odds = payload["odds"]
    assert set(odds) == ODDS_FIELDS
    assert odds["bookmaker"] == "pinnacle"
    assert odds["is_closing"] is False
    assert (odds["home"], odds["draw"], odds["away"]) == (2.0, 3.5, 4.0)
    assert odds["implied"] == pytest.approx(implied_probabilities(2.0, 3.5, 4.0))
    assert sum(odds["implied"].values()) == pytest.approx(1.0)
    assert odds["implied"] != pytest.approx(implied_probabilities(1.9, 3.6, 4.2))


def test_the_preview_excludes_a_same_day_and_a_later_meeting(
    api_client: TestClient, db_session: Session
) -> None:
    """L15: form and head-to-head read strictly before the fixture's own date.

    A scheduled fixture and a played one are both checked, because the second is
    the case the rule exists for: its preview shows the view of it before
    kickoff, so its own result and every later meeting of the pair are absent.
    """
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    earlier_season = create_season(
        db_session, code=OTHER_SEASON_CODE, start_year=OTHER_SEASON_START_YEAR
    )
    home = create_team(db_session, name="Synthetic L15 Home FC")
    away = create_team(db_session, name="Synthetic L15 Away FC")
    other_home = create_team(db_session, name="Synthetic L15 Played Home FC")
    other_away = create_team(db_session, name="Synthetic L15 Played Away FC")

    before = _played(
        db_session, season, home=away, away=home, days_ago=6, home_goals=1, away_goals=1
    )
    same_day = _played(
        db_session, earlier_season, home=away, away=home, days_ago=-3, home_goals=0, away_goals=2
    )
    later = _played(
        db_session, earlier_season, home=home, away=away, days_ago=-10, home_goals=5, away_goals=0
    )
    scheduled = _fixture(
        db_session, season, home=home, away=away, kickoff_date=today + _days(3), matchday=12
    )

    played_before = _played(
        db_session,
        season,
        home=other_away,
        away=other_home,
        days_ago=5,
        home_goals=0,
        away_goals=3,
    )
    played_same_day = _played(
        db_session,
        earlier_season,
        home=other_away,
        away=other_home,
        days_ago=-3,
        home_goals=0,
        away_goals=4,
    )
    played_later = _played(
        db_session,
        earlier_season,
        home=other_home,
        away=other_away,
        days_ago=-9,
        home_goals=3,
        away_goals=0,
    )
    played_fixture = _played(
        db_session,
        season,
        home=other_home,
        away=other_away,
        days_ago=-3,
        home_goals=2,
        away_goals=0,
    )

    scheduled_payload = _preview(api_client, scheduled.id)
    scheduled_form = scheduled_payload["home_form"]

    assert scheduled_payload["match"]["status"] == "scheduled"
    assert [match["id"] for match in scheduled_form["matches"]] == [before.id]
    assert scheduled_form["results"] == ["D"]
    assert (scheduled_form["points"], scheduled_form["goals_for"]) == (1, 1)
    assert [match["id"] for match in scheduled_payload["away_form"]["matches"]] == [before.id]
    listed = [match["id"] for match in scheduled_payload["head_to_head"]["matches"]]
    assert listed == [before.id]
    assert scheduled_payload["head_to_head"]["played"] == 1
    assert scheduled_payload["head_to_head"]["draws"] == 1
    for excluded in (same_day, later, scheduled):
        assert excluded.id not in listed

    played_payload = _preview(api_client, played_fixture.id)
    played_form = played_payload["home_form"]

    assert played_payload["match"]["status"] == "played"
    assert (played_payload["match"]["home_goals"], played_payload["match"]["result"]) == (2, "H")
    # The fixture's own result and the later meeting are served nowhere in the
    # record: only the meeting dated before it survives, from either side.
    assert [match["id"] for match in played_form["matches"]] == [played_before.id]
    assert played_form["results"] == ["W"]
    assert (played_form["goals_for"], played_form["goals_against"]) == (3, 0)
    assert [match["id"] for match in played_payload["away_form"]["matches"]] == [played_before.id]
    played_listed = [match["id"] for match in played_payload["head_to_head"]["matches"]]
    assert played_listed == [played_before.id]
    assert played_payload["head_to_head"]["played"] == 1
    assert played_payload["head_to_head"]["home_side_wins"] == 1
    for excluded in (played_same_day, played_later, played_fixture):
        assert excluded.id not in played_listed


def test_the_record_is_counted_from_the_fixtures_home_side(
    api_client: TestClient, db_session: Session
) -> None:
    """A pair that met twice gives a different answer per venue and per home side."""
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    earlier_season = create_season(
        db_session, code=OTHER_SEASON_CODE, start_year=OTHER_SEASON_START_YEAR
    )
    home = create_team(db_session, name="Synthetic Perspective Home FC")
    away = create_team(db_session, name="Synthetic Perspective Away FC")
    # The fixture's home side loses as the visitor of the first meeting...
    _played(db_session, season, home=away, away=home, days_ago=30, home_goals=1, away_goals=0)
    # ...wins as the home side of the second...
    _played(db_session, season, home=home, away=away, days_ago=20, home_goals=3, away_goals=0)
    # ...and draws the third, again as the visitor.
    _played(
        db_session, earlier_season, home=away, away=home, days_ago=10, home_goals=2, away_goals=2
    )
    fixture = _fixture(
        db_session,
        earlier_season,
        home=home,
        away=away,
        kickoff_date=today + _days(4),
        matchday=13,
    )

    payload = _preview(api_client, fixture.id)

    head_to_head = payload["head_to_head"]
    assert head_to_head["played"] == 3
    # A count by the venue of each meeting would read the first two as two home
    # wins and no away win; the served counters follow this fixture's home team.
    assert head_to_head["home_side_wins"] == 1
    assert head_to_head["draws"] == 1
    assert head_to_head["away_side_wins"] == 1


def test_a_scored_forecast_is_served_as_scored_and_a_withdrawn_one_as_withdrawn(
    api_client: TestClient, db_session: Session
) -> None:
    """The standing row of each model is reported with the outcome it earned."""
    today = madrid_today(db_session)
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    home = create_team(db_session, name="Synthetic Settled Home FC")
    away = create_team(db_session, name="Synthetic Settled Away FC")
    withdrawn_home = create_team(db_session, name="Synthetic Dropped Home FC")
    withdrawn_away = create_team(db_session, name="Synthetic Dropped Away FC")

    played = _fixture(
        db_session, season, home=home, away=away, kickoff_date=today + _days(3), matchday=14
    )
    stored = create_prediction(
        db_session, match=played, model="dixon_coles", probabilities=(0.2, 0.3, 0.5)
    )
    mark_played(db_session, played, home_goals=0, away_goals=2)

    withdrawn = _fixture(
        db_session,
        season,
        home=withdrawn_home,
        away=withdrawn_away,
        kickoff_date=today + _days(4),
        matchday=15,
    )
    create_prediction(db_session, match=withdrawn, model="home_win")
    mark_withdrawn(db_session, withdrawn)

    played_payload = _preview(api_client, played.id)

    assert played_payload["match"]["status"] == "played"
    brief = played_payload["predictions"][0]
    assert brief["prediction_id"] == stored.id
    assert brief["evaluation"] == "scored"
    detail = api_client.get(f"/v1/predictions/{brief['prediction_id']}")
    assert detail.status_code == 200
    assert detail.json()["evaluation"] == "scored"
    assert detail.json()["score"] is not None

    withdrawn_payload = _preview(api_client, withdrawn.id)

    assert withdrawn_payload["match"]["status"] == "withdrawn"
    assert [item["evaluation"] for item in withdrawn_payload["predictions"]] == ["withdrawn"]


def test_an_unknown_match_is_not_found_without_echoing_the_identifier(
    api_client: TestClient,
) -> None:
    unknown = 2_147_483_647

    response = api_client.get(PREVIEW_PATH.format(match_id=unknown))

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Not Found"
    assert problem["status"] == 404
    assert problem["detail"] == "Match not found"
    assert problem["errors"] is None
    assert str(unknown) not in response.text


def test_a_non_integer_match_id_is_unprocessable_without_echoing_the_value(
    api_client: TestClient,
) -> None:
    response = api_client.get("/v1/matches/not-a-match/preview")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Unprocessable Content"
    assert problem["status"] == 422
    assert problem["detail"] == "Request validation failed"
    errors = problem["errors"]
    assert errors
    assert ["path", "match_id"] in [error["loc"] for error in errors]
    assert "not-a-match" not in response.text


def test_openapi_documents_the_match_preview_contract() -> None:
    document = create_app().openapi()
    operation = document["paths"]["/v1/matches/{match_id}/preview"]["get"]

    assert operation["operationId"] == "get_match_preview"
    # The nested resource shares the tag of the match resource it describes, so a
    # generated client sees one group per entity and not one per path shape.
    assert operation["tags"] == ["matches"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": MATCH_PREVIEW_REF
    }
    for status in ("404", "422"):
        content = operation["responses"][status]["content"]
        assert PROBLEM_MEDIA_TYPE in content
        assert any(media.get("schema") == {"$ref": PROBLEM_REF} for media in content.values())

    assert document["paths"]["/v1/matches/{match_id}"]["get"]["operationId"] == "get_match"
    assert document["paths"]["/v1/matches/{match_id}"]["get"]["tags"] == ["matches"]
    assert "HTTPValidationError" not in document["components"]["schemas"]


def _preview(api_client: TestClient, match_id: int) -> dict[str, Any]:
    """Return the payload of one successful preview request."""
    response = api_client.get(PREVIEW_PATH.format(match_id=match_id))
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _fixture(
    session: Session,
    season: Season,
    *,
    home: Team,
    away: Team,
    kickoff_date: datetime.date,
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
    days_ago: int,
    home_goals: int,
    away_goals: int,
) -> Match:
    """Store one played match dated ``days_ago`` before Madrid today.

    A negative ``days_ago`` dates the match after today, which is how a test
    builds the same-day and the later meetings a preview has to exclude.
    """
    return create_match(
        session,
        season=season,
        home=home,
        away=away,
        kickoff_date=madrid_today(session) - _days(days_ago),
        status="played",
        home_goals=home_goals,
        away_goals=away_goals,
    )


def _days(count: int) -> datetime.timedelta:
    """Return ``count`` whole days as a timedelta."""
    return datetime.timedelta(days=count)
