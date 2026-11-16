"""Contract tests for the ``/v1/teams/{team_id}/season-stats`` endpoint.

The suite shares its database with the seasons and the matches the real
ingestion left behind, so every test bounds its requests with the synthetic
season code it created and asserts on the rows it stored itself, never on the
absolute size of a table.

The season of the main test holds six clubs, so ``ceil(6 / 3) = 2`` puts two
clubs in each tier and the tier membership is reachable by hand. Every expected
number -- the points, the averages, the venue records, the three tier records,
the expected-points sum and each rolling window -- is computed from the rows
``_build_world`` inserts and written here as arithmetic, never taken from the
code under test. The fixture dates are fixed in 2098 so the chronological order
and the rolling windows do not depend on the day the suite runs.

A statistics row is optional and a price is optional, so the world stores both
on some matches only: the payload must keep a missing value null, never zero,
and must leave an unpriced fixture out of the expected-points counter.

The six-club world cannot reach three of the rules, so three smaller seasons
pin them: a fixture whose more trusted price is inserted *after* a lower-priority
one, a season whose requested club only has a scheduled fixture, and a four-club
season whose bottom tier is empty because ``ceil(4 / 3) = 2`` fills the other two.
A fourth test reaches the default season's own 404, which needs a database
holding no match at all; the immutability trigger refuses to empty the match
table, so the test states that precondition instead of forcing it.
"""

import datetime
from typing import Any, NamedTuple

import pytest
from fastapi.testclient import TestClient
from lmi_api.main import create_app
from lmi_core.db.models import Match, MatchOdd, Season, Team
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.factories import create_match, create_season, create_team
from tests.live_metrics_support import store_odds
from tests.season_stats_support import create_match_stats

pytestmark = pytest.mark.integration

SEASON_CODE = "2098-99"
"""The synthetic season code of these tests: no data source covers 2098."""

SEASON_START_YEAR = 2098
"""The start year of :data:`SEASON_CODE`, which the season table keeps unique."""

OTHER_SEASON_CODE = "2097-98"
"""A second synthetic season, used to prove that a club of one is not of the other."""

PATH = "/v1/teams/{team_id}/season-stats"
"""The path under test, formatted with a team identifier."""

TEAM_SEASON_STATS_REF = "#/components/schemas/TeamSeasonStats"
PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type every error response is sent with."""

STAT_LINE_FIELDS = {
    "goals",
    "shots",
    "shots_on_target",
    "fouls",
    "corners",
    "yellow",
    "red",
    "xg",
}
ROLLING_FIELDS = {"points_pg", "xpts_pg", "goals_for_pg", "goals_against_pg"}
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
TIER_FIELDS = SPLIT_FIELDS | {"team_ids"}
MATCH_ROW_FIELDS = {
    "match",
    "venue",
    "points",
    "xpts",
    "goals_for",
    "goals_against",
    "shots_for",
    "shots_against",
    "rolling5",
}
TEAM_SEASON_STATS_FIELDS = {
    "team",
    "season",
    "played",
    "points",
    "xpts_market",
    "xpts_coverage",
    "per_match",
    "league_avg",
    "stat_coverage",
    "xg_coverage",
    "splits",
    "tiers",
    "matches",
}
"""The field sets of the payload and its nested objects, so a silent addition is visible."""

XPTS_FIRST = 50 / 29
"""Expected points of the focus club in match 1.

The opening Pinnacle price is ``2.0 / 3.5 / 4.0``; its inverse prices sum to
``29 / 28``, so ``p_home = (1 / 2) / (29 / 28)`` and ``p_draw = (2 / 7) /
(29 / 28)``. The club was at home, hence ``3 * p_home + p_draw = 50 / 29``.
The closing ``1.5 / 4.0 / 6.0`` row of the same fixture must not be read.
"""

XPTS_SECOND = 25 / 17
"""Expected points of the focus club in match 2, as the visitor at Gamma.

The only opening row is the lower-priority bet365 ``3.0 / 3.2 / 2.4``; its
inverse prices sum to ``17 / 16``, so ``3 * p_away + p_draw = 25 / 17``.
"""

XPTS_FOURTH = 3025 / 2343
"""Expected points of the focus club in match 4, as the visitor at Epsilon.

The market-average opening price is ``2.5 / 3.1 / 2.8``: the inverse sum is
``2343 / 2170``, so ``3 * p_away + p_draw = 3025 / 2343``.
"""

XPTS_PRIORITY_WINS = 29 / 29
"""Expected points of the focus club in match 2 once a Pinnacle row is added.

Match 2 then carries both the bet365 ``3.0 / 3.2 / 2.4`` row of the world and a
Pinnacle ``2.0 / 3.5 / 4.0`` row stored after it, so a reader that took the
first stored row would still answer :data:`XPTS_SECOND`. The Pinnacle inverse
prices sum to ``29 / 28``, so the visitor's
``3 * p_away + p_draw = 3 * (1/4) / (29/28) + (2/7) / (29/28) = 29 / 29``.
"""


class World(NamedTuple):
    """One synthetic season, with its rows in the chronological order of the payload."""

    season: Season
    focus: Team
    beta: Team
    gamma: Team
    delta: Team
    epsilon: Team
    zeta: Team
    played: list[Match]


def test_the_full_season_stats_payload_of_a_hand_computed_season(
    api_client: TestClient, db_session: Session
) -> None:
    world = _build_world(db_session)

    payload = _stats(api_client, world.focus.id, world.season.code)

    assert set(payload) == TEAM_SEASON_STATS_FIELDS
    assert payload["team"] == {"id": world.focus.id, "name": "Synthetic Season FC"}
    assert payload["season"] == SEASON_CODE
    assert payload["played"] == 6
    assert payload["points"] == 11
    assert payload["xpts_market"] == pytest.approx(XPTS_FIRST + XPTS_SECOND + XPTS_FOURTH)
    assert payload["xpts_coverage"] == 3
    assert payload["stat_coverage"] == 3
    assert payload["xg_coverage"] == 2

    assert set(payload["per_match"]) == {"for", "against"}
    _assert_line(
        payload["per_match"]["for"],
        goals=8 / 6,
        shots=(12 + 11 + 14) / 3,
        shots_on_target=(5 + 4 + 7) / 3,
        fouls=(10 + 9 + 13) / 3,
        corners=(6 + 7 + 8) / 3,
        yellow=(1 + 1 + 1) / 3,
        red=(0 + 0 + 1) / 3,
        xg=(1.80 + 1.40) / 2,
    )
    _assert_line(
        payload["per_match"]["against"],
        goals=5 / 6,
        shots=(8 + 9 + 10) / 3,
        shots_on_target=(2 + 4 + 4) / 3,
        fouls=(14 + 12 + 11) / 3,
        corners=(3 + 5 + 4) / 3,
        yellow=(2 + 2 + 3) / 3,
        red=(0 + 0 + 0) / 3,
        xg=(0.60 + 1.20) / 2,
    )
    _assert_line(
        payload["league_avg"],
        goals=(2 + 2 + 1 + 5 + 1 + 2) / 12,
        shots=(12 + 8 + 9 + 11 + 10 + 14) / 6,
        shots_on_target=(5 + 2 + 4 + 4 + 4 + 7) / 6,
        fouls=(10 + 14 + 12 + 9 + 11 + 13) / 6,
        corners=(6 + 3 + 5 + 7 + 4 + 8) / 6,
        yellow=(1 + 2 + 2 + 1 + 3 + 1) / 6,
        red=(0 + 0 + 0 + 0 + 0 + 1) / 6,
        xg=(1.80 + 0.60 + 1.20 + 1.40) / 4,
    )

    _assert_record(
        payload["splits"]["home"], played=3, won=2, drawn=0, lost=1, goals=(3, 1), points=6, ppg=2.0
    )
    _assert_record(
        payload["splits"]["away"],
        played=3,
        won=1,
        drawn=2,
        lost=0,
        goals=(5, 4),
        points=5,
        ppg=5 / 3,
    )

    tiers = payload["tiers"]
    _assert_tier(
        tiers["top"],
        team_ids=[world.focus.id, world.delta.id],
        played=2,
        won=0,
        drawn=1,
        lost=1,
        goals=(1, 2),
        points=1,
        ppg=0.5,
    )
    _assert_tier(
        tiers["middle"],
        team_ids=[world.gamma.id, world.epsilon.id],
        played=2,
        won=1,
        drawn=1,
        lost=0,
        goals=(4, 3),
        points=4,
        ppg=2.0,
    )
    _assert_tier(
        tiers["bottom"],
        team_ids=[world.beta.id, world.zeta.id],
        played=2,
        won=2,
        drawn=0,
        lost=0,
        goals=(3, 0),
        points=6,
        ppg=3.0,
    )

    rows = payload["matches"]
    for row in rows:
        assert set(row) == MATCH_ROW_FIELDS
        assert set(row["rolling5"]) == ROLLING_FIELDS
    assert [row["match"]["id"] for row in rows] == [match.id for match in world.played]
    assert [row["venue"] for row in rows] == ["home", "away", "home", "away", "home", "away"]
    assert [row["points"] for row in rows] == [3, 1, 0, 3, 3, 1]
    assert [row["goals_for"] for row in rows] == [2, 1, 0, 3, 1, 1]
    assert [row["goals_against"] for row in rows] == [0, 1, 1, 2, 0, 1]
    assert [row["shots_for"] for row in rows] == [12, 11, None, 14, None, None]
    assert [row["shots_against"] for row in rows] == [8, 9, None, 10, None, None]
    assert rows[0]["xpts"] == pytest.approx(XPTS_FIRST)
    assert rows[1]["xpts"] == pytest.approx(XPTS_SECOND)
    assert rows[2]["xpts"] is None
    assert rows[3]["xpts"] == pytest.approx(XPTS_FOURTH)
    assert rows[4]["xpts"] is None
    assert rows[5]["xpts"] is None

    rolling = [row["rolling5"] for row in rows]
    assert rolling[0] == {
        "points_pg": None,
        "xpts_pg": None,
        "goals_for_pg": None,
        "goals_against_pg": None,
    }
    _assert_rolling(rolling[1], points=3.0, xpts=XPTS_FIRST, goals=(2.0, 0.0))
    _assert_rolling(rolling[2], points=2.0, xpts=(XPTS_FIRST + XPTS_SECOND) / 2, goals=(1.5, 0.5))
    _assert_rolling(
        rolling[3], points=4 / 3, xpts=(XPTS_FIRST + XPTS_SECOND) / 2, goals=(1.0, 2 / 3)
    )
    _assert_rolling(
        rolling[4],
        points=1.75,
        xpts=(XPTS_FIRST + XPTS_SECOND + XPTS_FOURTH) / 3,
        goals=(1.5, 1.0),
    )
    _assert_rolling(
        rolling[5],
        points=2.0,
        xpts=(XPTS_FIRST + XPTS_SECOND + XPTS_FOURTH) / 3,
        goals=(1.4, 0.8),
    )


def test_the_price_rule_ignores_a_closing_row_and_uses_the_best_pre_closing_row(
    api_client: TestClient, db_session: Session
) -> None:
    world = _build_world(db_session)

    payload = _stats(api_client, world.focus.id, world.season.code)

    xpts = {row["match"]["id"]: row["xpts"] for row in payload["matches"]}
    # Match 1 carries both an opening and a closing Pinnacle row. The closing
    # one has the same bookmaker, so using it would change the value here.
    assert xpts[world.played[0].id] == pytest.approx(XPTS_FIRST)
    # Match 2 carries only a bet365 opening row: lower priority, but eligible.
    assert xpts[world.played[1].id] == pytest.approx(XPTS_SECOND)
    # Match 3 carries a closing row only, which must never be read.
    assert xpts[world.played[2].id] is None
    # Match 4 carries an opening market-average row.
    assert xpts[world.played[3].id] == pytest.approx(XPTS_FOURTH)
    # Matches 5 and 6 carry no price at all.
    assert xpts[world.played[4].id] is None
    assert xpts[world.played[5].id] is None
    assert payload["xpts_coverage"] == 3
    assert payload["xpts_market"] == pytest.approx(XPTS_FIRST + XPTS_SECOND + XPTS_FOURTH)


def test_the_price_priority_decides_and_not_the_stored_order(
    api_client: TestClient, db_session: Session
) -> None:
    """The most trusted bookmaker wins even when its row is stored last.

    Every fixture of the main world carries one eligible row at most, so a read
    that kept the first stored row of a fixture instead of following
    ``ODDS_PRIORITY`` would serve the same payload there. Match 2 is therefore
    given a second, more trusted row after the lower-priority one already on it,
    which makes the two rules disagree: priority answers ``29 / 29`` and the
    stored order answers ``25 / 17``.
    """
    world = _build_world(db_session)
    lower = db_session.scalars(
        select(MatchOdd).where(
            MatchOdd.match_id == world.played[1].id, MatchOdd.bookmaker == "bet365"
        )
    ).one()
    trusted = store_odds(
        db_session,
        match=world.played[1],
        bookmaker="pinnacle",
        home=2.0,
        draw=3.5,
        away=4.0,
    )
    # The bet365 row of the world is now the older of the two, so the identifier
    # order is the wrong rule's order and the two answers can never coincide.
    assert trusted.id > lower.id

    payload = _stats(api_client, world.focus.id, world.season.code)

    xpts = {row["match"]["id"]: row["xpts"] for row in payload["matches"]}
    assert xpts[world.played[1].id] == pytest.approx(XPTS_PRIORITY_WINS)
    assert xpts[world.played[1].id] != pytest.approx(XPTS_SECOND)
    # The second eligible row of one fixture is not a second priced fixture: the
    # counter counts fixtures, and the sum gains the one trusted value only.
    assert payload["xpts_coverage"] == 3
    assert payload["xpts_market"] == pytest.approx(XPTS_FIRST + XPTS_PRIORITY_WINS + XPTS_FOURTH)
    assert len(payload["matches"]) == 6
    assert xpts[world.played[0].id] == pytest.approx(XPTS_FIRST)
    assert xpts[world.played[3].id] == pytest.approx(XPTS_FOURTH)


def test_a_club_with_only_a_scheduled_fixture_is_served_as_an_empty_season(
    api_client: TestClient, db_session: Session
) -> None:
    """A club of the season that has not played yet is a 200, not a 404.

    Membership of a season is every club that appears in any match of it,
    whatever the status, so a club whose only fixture is still to come is a
    member with no played match. The season also holds one played fixture
    between two other clubs, so the league baseline and the statistics read are
    not empty while the club's own season is: the payload must answer zeros and
    nulls for the club without borrowing the other fixture's rows.
    """
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    focus = create_team(db_session, name="Synthetic Idle FC")
    waiting = create_team(db_session, name="Synthetic Waiting FC")
    host = create_team(db_session, name="Synthetic Host FC")
    guest = create_team(db_session, name="Synthetic Guest FC")
    create_match(
        db_session,
        season=season,
        home=focus,
        away=waiting,
        kickoff_date=datetime.date(2098, 8, 1),
    )
    played = create_match(
        db_session,
        season=season,
        home=host,
        away=guest,
        kickoff_date=datetime.date(2098, 8, 2),
        status="played",
        home_goals=1,
        away_goals=1,
    )
    create_match_stats(
        db_session,
        match=played,
        home_shots=10,
        away_shots=6,
        home_shots_on_target=4,
        away_shots_on_target=2,
        home_fouls=10,
        away_fouls=12,
        home_corners=5,
        away_corners=3,
        home_yellow=1,
        away_yellow=2,
        home_red=0,
        away_red=0,
    )
    store_odds(db_session, match=played, bookmaker="pinnacle", home=2.0, draw=3.5, away=4.0)

    payload = _stats(api_client, focus.id, season.code)

    assert payload["season"] == SEASON_CODE
    assert payload["team"] == {"id": focus.id, "name": "Synthetic Idle FC"}
    assert payload["played"] == 0
    assert payload["points"] == 0
    assert payload["xpts_market"] is None
    assert payload["xpts_coverage"] == 0
    assert payload["matches"] == []
    # An average over no match is null in every field, the goals included: a
    # zero here would invent a 0-0 season.
    _assert_line(
        payload["per_match"]["for"],
        goals=None,
        shots=None,
        shots_on_target=None,
        fouls=None,
        corners=None,
        yellow=None,
        red=None,
        xg=None,
    )
    _assert_line(
        payload["per_match"]["against"],
        goals=None,
        shots=None,
        shots_on_target=None,
        fouls=None,
        corners=None,
        yellow=None,
        red=None,
        xg=None,
    )
    # Both coverage counters count the club's own played matches. The season
    # holds one statistics row, on the fixture between the two other clubs, and
    # a count taken over the season instead of over the club would answer 1 for
    # both of them.
    assert payload["stat_coverage"] == 0
    assert payload["xg_coverage"] == 0
    for side in ("home", "away"):
        _assert_record(
            payload["splits"][side],
            played=0,
            won=0,
            drawn=0,
            lost=0,
            goals=(0, 0),
            points=0,
            ppg=None,
        )
    # The baseline averages both sides of the one played fixture: one goal each,
    # (10 + 6) / 2 shots, (4 + 2) / 2 on target, (10 + 12) / 2 fouls,
    # (5 + 3) / 2 corners, (1 + 2) / 2 yellow and (0 + 0) / 2 red. No side
    # carries an expected-goals value, so that field is null and not zero.
    _assert_line(
        payload["league_avg"],
        goals=(1 + 1) / 2,
        shots=(10 + 6) / 2,
        shots_on_target=(4 + 2) / 2,
        fouls=(10 + 12) / 2,
        corners=(5 + 3) / 2,
        yellow=(1 + 2) / 2,
        red=(0 + 0) / 2,
        xg=None,
    )
    # Four clubs put two in the top tier and two in the middle: the hosts and
    # the guests each hold the point of the draw, the focus club and its
    # scheduled opponent hold none.
    assert set(payload["tiers"]) == {"top", "middle", "bottom"}
    _assert_tier(
        payload["tiers"]["top"],
        team_ids=[host.id, guest.id],
        played=0,
        won=0,
        drawn=0,
        lost=0,
        goals=(0, 0),
        points=0,
        ppg=None,
    )
    _assert_tier(
        payload["tiers"]["middle"],
        team_ids=[focus.id, waiting.id],
        played=0,
        won=0,
        drawn=0,
        lost=0,
        goals=(0, 0),
        points=0,
        ppg=None,
    )
    _assert_tier(
        payload["tiers"]["bottom"],
        team_ids=[],
        played=0,
        won=0,
        drawn=0,
        lost=0,
        goals=(0, 0),
        points=0,
        ppg=None,
    )


def test_an_empty_tier_is_served_rather_than_dropped(
    api_client: TestClient, db_session: Session
) -> None:
    """A four-club table leaves the bottom tier empty and still serves it.

    The boundary is ``ceil(n / 3)``, so four clubs fill the top and the middle
    and leave the bottom with no member. A payload that omitted the empty tier
    would make a client paint two tiers of three, and its matches would no
    longer be partitioned by the records that are served.
    """
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    focus = create_team(db_session, name="Synthetic Tiered FC")
    alpha = create_team(db_session, name="Synthetic Alpha FC")
    beta = create_team(db_session, name="Synthetic Beta FC")
    gamma = create_team(db_session, name="Synthetic Gamma FC")
    _played(db_session, season, home=focus, away=alpha, home_goals=3, away_goals=0, day=1)
    _played(db_session, season, home=beta, away=focus, home_goals=1, away_goals=1, day=2)
    # The fourth club needs a fixture to be a member of the season; a scheduled
    # one is enough, and it carries no result into the table.
    create_match(
        db_session,
        season=season,
        home=gamma,
        away=alpha,
        kickoff_date=datetime.date(2098, 8, 3),
        matchday=3,
    )

    payload = _stats(api_client, focus.id, season.code)

    # The focus club won 3-0 and drew 1-1, so the table is the focus club on 4
    # points, Beta on 1, and Gamma and Alpha on none: positions 1-2 are the
    # focus club and Beta, positions 3-4 Gamma and Alpha, and no club is left for
    # the bottom tier.
    assert payload["played"] == 2
    assert payload["points"] == 4
    assert set(payload["tiers"]) == {"top", "middle", "bottom"}
    # Focus club at home against Alpha (a middle-tier club): one win, 3-0.
    _assert_tier(
        payload["tiers"]["middle"],
        team_ids=[alpha.id, gamma.id],
        played=1,
        won=1,
        drawn=0,
        lost=0,
        goals=(3, 0),
        points=3,
        ppg=3.0,
    )
    # Focus club away at Beta (a top-tier club): one draw, 1-1.
    _assert_tier(
        payload["tiers"]["top"],
        team_ids=[focus.id, beta.id],
        played=1,
        won=0,
        drawn=1,
        lost=0,
        goals=(1, 1),
        points=1,
        ppg=1.0,
    )
    _assert_tier(
        payload["tiers"]["bottom"],
        team_ids=[],
        played=0,
        won=0,
        drawn=0,
        lost=0,
        goals=(0, 0),
        points=0,
        ppg=None,
    )
    tiers = payload["tiers"]
    assert sum(tiers[name]["played"] for name in ("top", "middle", "bottom")) == payload["played"]


def test_splits_and_points_agree_with_the_standings_and_focus_endpoints(
    api_client: TestClient, db_session: Session
) -> None:
    world = _build_world(db_session)

    payload = _stats(api_client, world.focus.id, world.season.code)
    standings = api_client.get("/v1/standings", params={"season": world.season.code})
    assert standings.status_code == 200
    row = next(row for row in standings.json()["rows"] if row["team"]["id"] == world.focus.id)

    home = payload["splits"]["home"]
    away = payload["splits"]["away"]
    for field in ("played", "won", "drawn", "lost", "goals_for", "goals_against", "points"):
        assert home[field] + away[field] == row[field], field
    assert payload["points"] == row["points"]

    focus = api_client.get(
        f"/v1/teams/{world.focus.id}/focus", params={"season": world.season.code}
    )
    assert focus.status_code == 200
    focus_payload = focus.json()
    assert payload["splits"] == focus_payload["splits"]
    assert payload["points"] == focus_payload["standing"]["points"]


def test_unknown_team_unknown_season_and_a_team_of_another_season_are_not_found(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    other = create_season(db_session, code=OTHER_SEASON_CODE, start_year=SEASON_START_YEAR - 1)
    focus = create_team(db_session, name="Synthetic Focus FC")
    opponent = create_team(db_session, name="Synthetic Opponent FC")
    create_match(
        db_session,
        season=other,
        home=focus,
        away=opponent,
        kickoff_date=datetime.date(2097, 8, 1),
    )
    unknown = 2_147_483_647

    unknown_team = api_client.get(PATH.format(team_id=unknown))
    unknown_season = api_client.get(PATH.format(team_id=focus.id), params={"season": "1999-00"})
    other_season = api_client.get(PATH.format(team_id=focus.id), params={"season": season.code})

    for response, detail in (
        (unknown_team, "Team not found"),
        (unknown_season, "Season not found"),
        (other_season, "Team not found in season"),
    ):
        assert response.status_code == 404
        assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
        problem = response.json()
        assert problem["title"] == "Not Found"
        assert problem["status"] == 404
        assert problem["detail"] == detail
        assert problem["errors"] is None
    assert str(unknown) not in unknown_team.text
    assert "1999-00" not in unknown_season.text
    assert str(focus.id) not in other_season.text
    assert season.code not in other_season.text


def test_a_non_integer_team_id_is_unprocessable_without_echoing_the_value(
    api_client: TestClient,
) -> None:
    response = api_client.get("/v1/teams/not-a-team/season-stats")

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


def test_the_default_season_is_the_latest_one_with_a_match(
    api_client: TestClient, db_session: Session
) -> None:
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    create_season(db_session, code="2099-00", start_year=2099)
    focus = create_team(db_session, name="Synthetic Default FC")
    opponent = create_team(db_session, name="Synthetic Opponent FC")
    create_match(
        db_session,
        season=season,
        home=focus,
        away=opponent,
        kickoff_date=datetime.date(2098, 8, 1),
        status="played",
        home_goals=1,
        away_goals=0,
    )

    response = api_client.get(PATH.format(team_id=focus.id))

    assert response.status_code == 200
    assert response.json()["season"] == SEASON_CODE


def test_the_default_season_of_a_database_without_a_match_is_not_found(
    api_client: TestClient, db_session: Session
) -> None:
    """A default request with no match to open on is a fixed 404, not a 500.

    Without ``season`` the route resolves the latest season that has a match, and
    a database holding no match at all leaves that resolution nothing to answer.

    The state is a precondition rather than something this test builds: emptying
    the match table would be the direct way to reach it, but
    ``TRUNCATE TABLE matches CASCADE`` is refused by the ``lmi_reject_mutation``
    trigger, because the cascade truncates the append-only ``predictions`` table
    with it. A disposable database is migrated but never ingested, so it holds
    no match and the branch is reachable as it stands; a database the ingestion
    has filled cannot reach it at all, and the test says so instead of asserting
    a state it did not create.
    """
    season = create_season(db_session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    focus = create_team(db_session, name="Synthetic Seasonless FC")
    if db_session.scalar(select(func.count()).select_from(Match)):
        pytest.skip(
            "the database holds matches, and lmi_reject_mutation refuses "
            "'TRUNCATE TABLE matches CASCADE' because the cascade truncates predictions"
        )

    default = api_client.get(PATH.format(team_id=focus.id))
    explicit = api_client.get(PATH.format(team_id=focus.id), params={"season": season.code})

    assert default.status_code == 404
    assert default.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = default.json()
    assert problem["title"] == "Not Found"
    assert problem["status"] == 404
    assert problem["detail"] == "No season with matches"
    assert problem["errors"] is None
    # The named season still exists and simply has no match the club appears in,
    # so the sentence above can only come from the default resolution: the two
    # 404 branches are told apart.
    assert explicit.status_code == 404
    assert explicit.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert explicit.json()["detail"] == "Team not found in season"


def test_openapi_documents_the_team_season_stats_contract() -> None:
    document = create_app().openapi()
    operation = document["paths"]["/v1/teams/{team_id}/season-stats"]["get"]

    assert operation["operationId"] == "get_team_season_stats"
    assert operation["tags"] == ["teams"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": TEAM_SEASON_STATS_REF
    }
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert "season" in parameters
    for status in ("404", "422"):
        content = operation["responses"][status]["content"]
        assert PROBLEM_MEDIA_TYPE in content
        assert any(media.get("schema") == {"$ref": PROBLEM_REF} for media in content.values())

    schemas = document["components"]["schemas"]
    properties = schemas["TeamSeasonStats"]["properties"]
    for name in ("matches", "tiers", "per_match", "league_avg"):
        assert name in properties
    assert "for" in schemas["PerMatch"]["properties"]
    assert "HTTPValidationError" not in schemas


def _days(count: int) -> datetime.timedelta:
    """Return ``count`` whole days as a timedelta."""
    return datetime.timedelta(days=count)


def _stats(api_client: TestClient, team_id: int, season: str) -> dict[str, Any]:
    """Return the payload of one successful season-stats request."""
    response = api_client.get(PATH.format(team_id=team_id), params={"season": season})
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _assert_line(line: dict[str, Any], **expected: float | None) -> None:
    """Assert the field set and every hand-computed value of one average line."""
    assert set(line) == STAT_LINE_FIELDS
    for field, value in expected.items():
        if value is None:
            assert line[field] is None, field
        else:
            assert line[field] == pytest.approx(value), field


def _assert_record(
    record: dict[str, Any],
    *,
    played: int,
    won: int,
    drawn: int,
    lost: int,
    goals: tuple[int, int],
    points: int,
    ppg: float | None,
) -> None:
    """Assert the field set and every counter of one venue split."""
    assert set(record) == SPLIT_FIELDS
    _assert_counters(
        record,
        played=played,
        won=won,
        drawn=drawn,
        lost=lost,
        goals=goals,
        points=points,
        ppg=ppg,
    )


def _assert_counters(
    record: dict[str, Any],
    *,
    played: int,
    won: int,
    drawn: int,
    lost: int,
    goals: tuple[int, int],
    points: int,
    ppg: float | None,
) -> None:
    """Assert the hand-computed counters shared by a split and a tier record.

    ``ppg`` is ``None`` for a group that played no match, which is the null the
    schema promises instead of a made-up average.
    """
    assert record["played"] == played
    assert record["won"] == won
    assert record["drawn"] == drawn
    assert record["lost"] == lost
    assert (record["goals_for"], record["goals_against"]) == goals
    assert record["points"] == points
    if ppg is None:
        assert record["points_per_game"] is None
    else:
        assert record["points_per_game"] == pytest.approx(ppg)


def _assert_tier(
    record: dict[str, Any],
    *,
    team_ids: list[int],
    played: int,
    won: int,
    drawn: int,
    lost: int,
    goals: tuple[int, int],
    points: int,
    ppg: float | None,
) -> None:
    """Assert one tier record, its membership and its counters."""
    assert set(record) == TIER_FIELDS
    assert record["team_ids"] == team_ids
    _assert_counters(
        record,
        played=played,
        won=won,
        drawn=drawn,
        lost=lost,
        goals=goals,
        points=points,
        ppg=ppg,
    )


def _assert_rolling(
    line: dict[str, Any], *, points: float, xpts: float | None, goals: tuple[float, float]
) -> None:
    """Assert one rolling window's hand-computed averages."""
    assert set(line) == ROLLING_FIELDS
    assert line["points_pg"] == pytest.approx(points)
    if xpts is None:
        assert line["xpts_pg"] is None
    else:
        assert line["xpts_pg"] == pytest.approx(xpts)
    assert line["goals_for_pg"] == pytest.approx(goals[0])
    assert line["goals_against_pg"] == pytest.approx(goals[1])


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
    return create_match(
        session,
        season=season,
        home=home,
        away=away,
        kickoff_date=datetime.date(2098, 8, 1) + _days(day - 1),
        matchday=day,
        status="played",
        home_goals=home_goals,
        away_goals=away_goals,
    )


def _build_world(session: Session) -> World:
    """Store one synthetic six-club season and return its rows.

    The six results give the focus club six played matches, three at each venue,
    and the table, in order: the focus club on 11 points, Delta on 4, Gamma on
    1, then Epsilon, Zeta and Beta on none, separated by goal difference and
    goals scored. The top tier is therefore the focus club and Delta, the middle
    Gamma and Epsilon, and the bottom Zeta and Beta.

    Statistics rows cover matches 1, 2 and 4, with expected goals on the first
    two only. Match 1 carries an opening and a closing price, match 2 a single
    lower-priority opening price, match 3 a closing price only, match 4 an
    opening price, and matches 5 and 6 no price at all.
    """
    season = create_season(session, code=SEASON_CODE, start_year=SEASON_START_YEAR)
    focus = create_team(session, name="Synthetic Season FC")
    beta = create_team(session, name="Synthetic Beta FC")
    gamma = create_team(session, name="Synthetic Gamma FC")
    delta = create_team(session, name="Synthetic Delta FC")
    epsilon = create_team(session, name="Synthetic Epsilon FC")
    zeta = create_team(session, name="Synthetic Zeta FC")

    played = [
        _played(session, season, home=focus, away=beta, home_goals=2, away_goals=0, day=1),
        _played(session, season, home=gamma, away=focus, home_goals=1, away_goals=1, day=2),
        _played(session, season, home=focus, away=delta, home_goals=0, away_goals=1, day=3),
        _played(session, season, home=epsilon, away=focus, home_goals=2, away_goals=3, day=4),
        _played(session, season, home=focus, away=zeta, home_goals=1, away_goals=0, day=5),
        _played(session, season, home=delta, away=focus, home_goals=1, away_goals=1, day=6),
    ]

    create_match_stats(
        session,
        match=played[0],
        home_shots=12,
        away_shots=8,
        home_shots_on_target=5,
        away_shots_on_target=2,
        home_fouls=10,
        away_fouls=14,
        home_corners=6,
        away_corners=3,
        home_yellow=1,
        away_yellow=2,
        home_red=0,
        away_red=0,
        home_xg=1.80,
        away_xg=0.60,
    )
    create_match_stats(
        session,
        match=played[1],
        home_shots=9,
        away_shots=11,
        home_shots_on_target=4,
        away_shots_on_target=4,
        home_fouls=12,
        away_fouls=9,
        home_corners=5,
        away_corners=7,
        home_yellow=2,
        away_yellow=1,
        home_red=0,
        away_red=0,
        home_xg=1.20,
        away_xg=1.40,
    )
    create_match_stats(
        session,
        match=played[3],
        home_shots=10,
        away_shots=14,
        home_shots_on_target=4,
        away_shots_on_target=7,
        home_fouls=11,
        away_fouls=13,
        home_corners=4,
        away_corners=8,
        home_yellow=3,
        away_yellow=1,
        home_red=0,
        away_red=1,
    )

    store_odds(session, match=played[0], bookmaker="pinnacle", home=2.0, draw=3.5, away=4.0)
    store_odds(
        session,
        match=played[0],
        bookmaker="pinnacle",
        home=1.5,
        draw=4.0,
        away=6.0,
        is_closing=True,
    )
    store_odds(session, match=played[1], bookmaker="bet365", home=3.0, draw=3.2, away=2.4)
    store_odds(
        session,
        match=played[2],
        bookmaker="pinnacle",
        home=1.5,
        draw=4.0,
        away=6.0,
        is_closing=True,
    )
    store_odds(session, match=played[3], bookmaker="market_avg", home=2.5, draw=3.1, away=2.8)

    return World(
        season=season,
        focus=focus,
        beta=beta,
        gamma=gamma,
        delta=delta,
        epsilon=epsilon,
        zeta=zeta,
        played=played,
    )
