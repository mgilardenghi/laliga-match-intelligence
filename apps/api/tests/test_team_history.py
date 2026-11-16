"""Contract tests for the ``/v1/teams/{team_id}/history`` endpoint.

The suite shares its database with the seasons and the matches the real
ingestion left behind, so every test bounds its requests with the synthetic
season codes it created and asserts on the rows it stored itself, never on the
absolute size of a table.

The main test stores two seasons for one club and asserts the whole payload:
the season order, the table position of each season, the counters, every
per-game average, the cards that are yellow plus red, the expected points of the
one eligible pre-closing price, and the steepest-decline list with its sign. The
rows are inserted newest season first, so the served order cannot be the stored
order. Every expected number is computed from the rows ``_build_history``
inserts and written here as arithmetic, never taken from the code under test.

Three smaller worlds pin the states the two-season world cannot reach: a row
that records zero cards next to an unknown statistic, a club that played
nothing, and a club whose only fixture of a season is still scheduled. A fourth
test reaches the unknown-identifier 404, and the last pins the OpenAPI contract.
"""

import datetime
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, NamedTuple

import pytest
from fastapi.testclient import TestClient
from lmi_api.main import create_app
from lmi_core.db.models import Match, Season, Team
from sqlalchemy import event
from sqlalchemy.orm import Session

from tests.factories import create_match, create_season, create_team
from tests.live_metrics_support import store_odds
from tests.season_stats_support import create_match_stats

pytestmark = pytest.mark.integration

EARLIER_SEASON_CODE = "2096-97"
"""The earlier synthetic season of the main world: no data source covers 2096."""

LATER_SEASON_CODE = "2097-98"
"""The later synthetic season of the main world."""

ZERO_CARD_SEASON_CODE = "2095-96"
"""The synthetic season of the zero-cards test."""

IDLE_SEASON_CODE = "2094-95"
"""The synthetic season of the scheduled-only test."""

PATH = "/v1/teams/{team_id}/history"
"""The path under test, formatted with a team identifier."""

TEAM_HISTORY_REF = "#/components/schemas/TeamHistory"
PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type every error response is sent with."""

SEASON_FIELDS = {
    "season",
    "position",
    "played",
    "points",
    "points_pg",
    "goals_for_pg",
    "goals_against_pg",
    "shots_for_pg",
    "shots_against_pg",
    "sot_for_pg",
    "sot_against_pg",
    "cards_pg",
    "xpts_pg",
}
DROP_FIELDS = {"metric", "from_season", "to_season", "delta"}
TEAM_HISTORY_FIELDS = {"team", "seasons", "largest_drop"}
"""The field sets of the payload and its nested objects, so a silent addition is visible."""

XPTS_EARLIER = 50 / 29
"""Expected points of the focus club's home match in the earlier season.

The opening Pinnacle price is ``2.0 / 3.5 / 4.0``; its inverse prices sum to
``29 / 28``, so ``p_home = (1 / 2) / (29 / 28)`` and ``p_draw = (2 / 7) /
(29 / 28)``. The club was at home, hence ``3 * p_home + p_draw = 50 / 29``.
The closing ``1.5 / 4.0 / 6.0`` row of the same fixture must not be read.
"""


class History(NamedTuple):
    """The two synthetic seasons of the main world, newest first."""

    earlier: Season
    later: Season
    focus: Team
    alpha: Team
    beta: Team
    gamma: Team
    earlier_played: list[Match]
    later_played: list[Match]


def test_the_full_history_payload_of_two_hand_computed_seasons(
    api_client: TestClient, db_session: Session
) -> None:
    world = _build_history(db_session)

    payload = _history(api_client, world.focus.id)

    assert set(payload) == TEAM_HISTORY_FIELDS
    assert payload["team"] == {"id": world.focus.id, "name": "Synthetic History FC"}
    assert [line["season"] for line in payload["seasons"]] == [
        EARLIER_SEASON_CODE,
        LATER_SEASON_CODE,
    ]

    earlier, later = payload["seasons"]
    _assert_season(
        earlier,
        season=EARLIER_SEASON_CODE,
        position=2,
        played=2,
        points=4,
        points_pg=2.0,
        goals_for_pg=1.5,
        goals_against_pg=0.5,
        shots_for_pg=7.5,
        shots_against_pg=10.0,
        sot_for_pg=2.5,
        sot_against_pg=4.0,
        cards_pg=1.5,
        xpts_pg=XPTS_EARLIER,
    )
    _assert_season(
        later,
        season=LATER_SEASON_CODE,
        position=3,
        played=2,
        points=0,
        points_pg=0.0,
        goals_for_pg=0.0,
        goals_against_pg=2.0,
        shots_for_pg=5.5,
        shots_against_pg=11.5,
        sot_for_pg=None,
        sot_against_pg=None,
        cards_pg=2.0,
        xpts_pg=None,
    )

    drops = payload["largest_drop"]
    for drop in drops:
        assert set(drop) == DROP_FIELDS
    # The two -2.0 drops are ordered by metric name, then the three -1.5 ones
    # alphabetically, then the cards drop, which is the only -0.5 value. The
    # shots-on-target and expected-points metrics are absent because the later
    # season carries no value for them, not because they are zero.
    assert [(drop["metric"], drop["delta"]) for drop in drops] == [
        ("points_pg", pytest.approx(-2.0)),
        ("shots_for_pg", pytest.approx(-2.0)),
        ("goals_against_pg", pytest.approx(-1.5)),
        ("goals_for_pg", pytest.approx(-1.5)),
        ("shots_against_pg", pytest.approx(-1.5)),
        ("cards_pg", pytest.approx(-0.5)),
    ]
    assert {(drop["from_season"], drop["to_season"]) for drop in drops} == {
        (EARLIER_SEASON_CODE, LATER_SEASON_CODE)
    }
    assert all(drop["delta"] < 0 for drop in drops)


def test_zero_cards_is_a_counted_zero_and_not_a_missing_value(
    api_client: TestClient, db_session: Session
) -> None:
    """A row that records zero cards answers ``0.0`` beside a null statistic.

    ``cards_pg`` is yellow plus red, so a row with both columns at zero is a
    counted zero. The same row leaves shots on target unknown, and that must stay
    null: a read that folded one missing column into the card sum would answer a
    number for both.
    """
    season = create_season(db_session, code=ZERO_CARD_SEASON_CODE, start_year=2095)
    focus = create_team(db_session, name="Synthetic Cardless FC")
    opponent = create_team(db_session, name="Synthetic Cardful FC")
    match = create_match(
        db_session,
        season=season,
        home=focus,
        away=opponent,
        kickoff_date=datetime.date(2095, 8, 1),
        status="played",
        home_goals=1,
        away_goals=0,
    )
    create_match_stats(
        db_session,
        match=match,
        home_shots=5,
        away_shots=4,
        home_yellow=0,
        away_yellow=2,
        home_red=0,
        away_red=0,
    )

    payload = _history(api_client, focus.id)

    assert len(payload["seasons"]) == 1
    line = payload["seasons"][0]
    assert line["played"] == 1
    assert line["cards_pg"] == 0.0
    assert line["cards_pg"] is not None
    assert line["sot_for_pg"] is None
    assert line["sot_against_pg"] is None
    assert line["shots_for_pg"] == pytest.approx(5.0)


def test_a_club_that_played_nothing_serves_an_empty_history(
    api_client: TestClient, db_session: Session
) -> None:
    """A stored club without a played match is a 200, not a 404 and not zeros."""
    focus = create_team(db_session, name="Synthetic Unplayed FC")

    payload = _history(api_client, focus.id)

    assert payload == {
        "team": {"id": focus.id, "name": "Synthetic Unplayed FC"},
        "seasons": [],
        "largest_drop": [],
    }


def test_a_scheduled_fixture_does_not_list_its_season(
    api_client: TestClient, db_session: Session
) -> None:
    """A club whose only fixture is scheduled has no history in that season.

    The season also holds a played fixture between two other clubs, so the
    season exists and has a result; the focus club simply did not play it, and a
    line for that season would describe a season the club sat out.
    """
    season = create_season(db_session, code=IDLE_SEASON_CODE, start_year=2094)
    focus = create_team(db_session, name="Synthetic Waiting FC")
    waiting = create_team(db_session, name="Synthetic Scheduled FC")
    host = create_team(db_session, name="Synthetic Host FC")
    guest = create_team(db_session, name="Synthetic Guest FC")
    create_match(
        db_session,
        season=season,
        home=focus,
        away=waiting,
        kickoff_date=datetime.date(2094, 8, 1),
    )
    create_match(
        db_session,
        season=season,
        home=host,
        away=guest,
        kickoff_date=datetime.date(2094, 8, 2),
        status="played",
        home_goals=1,
        away_goals=1,
    )

    payload = _history(api_client, focus.id)

    assert payload["team"] == {"id": focus.id, "name": "Synthetic Waiting FC"}
    assert payload["seasons"] == []
    assert payload["largest_drop"] == []


def test_the_read_issues_a_fixed_number_of_statements(
    api_client: TestClient, db_session: Session
) -> None:
    """The read is bounded by its six statements, never by the payload size.

    The main world holds two seasons and four played matches, so a read that
    loaded one season or one match at a time would need more statements than
    this. A club without a played match stops after the club and its season
    list, which is the honest empty history. The count is taken on the
    connection the fixture session uses, so it observes what the request really
    issued and not what the code claims.
    """
    world = _build_history(db_session)
    idle = create_team(db_session, name="Synthetic Statementless FC")

    with _counted_statements(db_session) as history_statements:
        _history(api_client, world.focus.id)

    assert len(history_statements) == 6
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in history_statements)

    with _counted_statements(db_session) as empty_statements:
        _history(api_client, idle.id)

    assert len(empty_statements) == 2
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in empty_statements)


def test_an_unknown_team_id_is_not_found_without_echoing_the_value(
    api_client: TestClient,
) -> None:
    unknown = 2_147_483_647

    response = api_client.get(PATH.format(team_id=unknown))

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Not Found"
    assert problem["status"] == 404
    assert problem["detail"] == "Team not found"
    assert problem["errors"] is None
    assert str(unknown) not in response.text


def test_openapi_documents_the_team_history_contract() -> None:
    document = create_app().openapi()
    operation = document["paths"]["/v1/teams/{team_id}/history"]["get"]

    assert operation["operationId"] == "get_team_history"
    assert operation["tags"] == ["teams"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": TEAM_HISTORY_REF
    }
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert "season" not in parameters
    for status in ("404", "422"):
        content = operation["responses"][status]["content"]
        assert PROBLEM_MEDIA_TYPE in content
        assert any(media.get("schema") == {"$ref": PROBLEM_REF} for media in content.values())

    schemas = document["components"]["schemas"]
    for name in ("TeamHistory", "TeamHistorySeason", "TeamHistoryDrop"):
        assert name in schemas
    assert set(schemas["TeamHistory"]["properties"]) == TEAM_HISTORY_FIELDS
    assert set(schemas["TeamHistorySeason"]["properties"]) == SEASON_FIELDS
    assert set(schemas["TeamHistoryDrop"]["properties"]) == DROP_FIELDS


def _history(api_client: TestClient, team_id: int) -> dict[str, Any]:
    """Return the payload of one successful history request."""
    response = api_client.get(PATH.format(team_id=team_id))
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


@contextmanager
def _counted_statements(session: Session) -> Iterator[list[str]]:
    """Yield the statements the session executes inside the block, in order."""
    statements: list[str] = []

    def record(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(bind, "before_cursor_execute", record)


def _assert_season(
    line: dict[str, Any],
    *,
    season: str,
    position: int | None,
    played: int,
    points: int,
    points_pg: float,
    goals_for_pg: float,
    goals_against_pg: float,
    shots_for_pg: float | None,
    shots_against_pg: float | None,
    sot_for_pg: float | None,
    sot_against_pg: float | None,
    cards_pg: float | None,
    xpts_pg: float | None,
) -> None:
    """Assert the field set and every hand-computed value of one season line."""
    assert set(line) == SEASON_FIELDS
    assert line["season"] == season
    assert line["position"] == position
    assert line["played"] == played
    assert line["points"] == points
    for field, expected in (
        ("points_pg", points_pg),
        ("goals_for_pg", goals_for_pg),
        ("goals_against_pg", goals_against_pg),
        ("shots_for_pg", shots_for_pg),
        ("shots_against_pg", shots_against_pg),
        ("sot_for_pg", sot_for_pg),
        ("sot_against_pg", sot_against_pg),
        ("cards_pg", cards_pg),
        ("xpts_pg", xpts_pg),
    ):
        if expected is None:
            assert line[field] is None, field
        else:
            assert line[field] == pytest.approx(expected), field


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
    """Store one played match of a 2090s August, on the given day counted from 1."""
    return create_match(
        session,
        season=season,
        home=home,
        away=away,
        kickoff_date=datetime.date(season.start_year, 8, 1) + datetime.timedelta(days=day - 1),
        matchday=day,
        status="played",
        home_goals=home_goals,
        away_goals=away_goals,
    )


def _build_history(session: Session) -> History:
    """Store the two seasons of the main world and return their rows.

    The later season is inserted first, so a served order that matches the
    stored order would fail the assertion on the season codes.

    The earlier season is the focus club's first: it wins at home and draws
    away, which puts it second behind Beta on goal difference. The later season
    is a decline: it loses at home and away and finishes third, last in a
    three-club table. The statistics rows give the earlier season a known
    shots-on-target average and the later season a null one, while both seasons
    carry shots. Cards are the club's own yellow plus red on each side.
    """
    later = create_season(session, code=LATER_SEASON_CODE, start_year=2097)
    focus = create_team(session, name="Synthetic History FC")
    alpha = create_team(session, name="Synthetic Alpha FC")
    beta = create_team(session, name="Synthetic Beta FC")
    gamma = create_team(session, name="Synthetic Gamma FC")
    later_played = [
        _played(session, later, home=focus, away=alpha, home_goals=0, away_goals=1, day=1),
        _played(session, later, home=gamma, away=focus, home_goals=3, away_goals=0, day=2),
        _played(session, later, home=alpha, away=gamma, home_goals=2, away_goals=2, day=3),
    ]

    earlier = create_season(session, code=EARLIER_SEASON_CODE, start_year=2096)
    earlier_played = [
        _played(session, earlier, home=focus, away=alpha, home_goals=2, away_goals=0, day=1),
        _played(session, earlier, home=beta, away=focus, home_goals=1, away_goals=1, day=2),
        _played(session, earlier, home=alpha, away=beta, home_goals=0, away_goals=3, day=3),
    ]

    create_match_stats(
        session,
        match=earlier_played[0],
        home_shots=10,
        away_shots=8,
        home_shots_on_target=4,
        away_shots_on_target=2,
        home_yellow=1,
        away_yellow=2,
        home_red=0,
        away_red=1,
    )
    create_match_stats(
        session,
        match=earlier_played[1],
        home_shots=12,
        away_shots=5,
        home_shots_on_target=6,
        away_shots_on_target=1,
        home_yellow=3,
        away_yellow=2,
        home_red=1,
        away_red=0,
    )
    create_match_stats(
        session,
        match=later_played[0],
        home_shots=7,
        away_shots=9,
        home_yellow=2,
        away_yellow=1,
        home_red=0,
        away_red=0,
    )
    create_match_stats(
        session,
        match=later_played[1],
        home_shots=14,
        away_shots=4,
        home_yellow=1,
        away_yellow=1,
        home_red=0,
        away_red=1,
    )

    # The earlier home match has one eligible opening price and one closing row
    # that must never be read. The earlier away match has one opening row from a
    # bookmaker outside the priority list, which must be ignored too. The later
    # home match has a closing row only, so its expected points are unknown.
    store_odds(
        session,
        match=earlier_played[0],
        bookmaker="pinnacle",
        home=2.0,
        draw=3.5,
        away=4.0,
    )
    store_odds(
        session,
        match=earlier_played[0],
        bookmaker="pinnacle",
        home=1.5,
        draw=4.0,
        away=6.0,
        is_closing=True,
    )
    store_odds(
        session,
        match=earlier_played[1],
        bookmaker="market_max",
        home=1.1,
        draw=5.0,
        away=9.0,
    )
    store_odds(
        session,
        match=later_played[0],
        bookmaker="pinnacle",
        home=1.5,
        draw=4.0,
        away=6.0,
        is_closing=True,
    )

    return History(
        earlier=earlier,
        later=later,
        focus=focus,
        alpha=alpha,
        beta=beta,
        gamma=gamma,
        earlier_played=earlier_played,
        later_played=later_played,
    )
