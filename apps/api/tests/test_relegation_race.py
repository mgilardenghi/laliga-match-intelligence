"""Contract tests for the ``/v1/teams/{team_id}/relegation-race`` endpoint.

The suite shares its database with whatever the migrations seed, so every test
bounds its requests with the synthetic season codes it created and asserts on the
rows it stored itself, never on the absolute size of a table. The one number read
from the whole database is the survival threshold, which is a sample of every
stored season by design; the tests therefore build the only completed season
their database holds and assert the sample it produces.

The complete season of the main world is a twenty-club double round robin: the
club at index ``i`` beats every club with a higher index at both venues 1-0, so
it wins ``2 * (19 - i)`` matches and finishes on ``6 * (19 - i)`` points, every
place holds a distinct total and the table is the index order. The seventeenth
place therefore holds ``6 * (20 - 17) = 18`` points, and that arithmetic is
written in this module rather than read back from the route or the ranking.

Every fixture of that season is played, so the tests that serve a remaining
fixture build a second, still unfinished season out of four of the same clubs.
It starts one year later, so it is also the season a request without a code
resolves, and its own small table ranks those four clubs in a hand-checkable
order: Alpha beat Beta 2-0, Beta beat Gamma 1-0, and the subject has not played,
so Alpha leads on three points and a head-to-head win over Beta, Beta follows,
and the subject sits above Gamma on goal difference.

Six worlds pin the served states: a season with no simulation, a completed
season beside an incomplete one, a league too small to be counted, a season with
three succeeded runs and a failed one, a run without a row for the subject club,
and the three not-found cases. The detail tests store the run's per-fixture
probabilities and next-match counts for the unfinished season, because that is
the world where the club still has fixtures to price: they pin the venue mapping,
a fixture the run did not simulate, the freshness guard, a never-sampled result,
the club's own row of the run, a run that stored no detail at all, a run that
holds no row for the club while the club still has a fixture, and a run whose
sample size is not the default. Two further tests pin the statement counts and
one the OpenAPI contract.
"""

import datetime
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, NamedTuple

import pytest
from fastapi.testclient import TestClient
from lmi_api.main import create_app
from lmi_core.db.models import (
    Match,
    Season,
    SimulationFixture,
    SimulationNextMatch,
    SimulationRun,
    Team,
)
from sqlalchemy import event
from sqlalchemy.orm import Session

from tests.factories import create_match, create_season, create_team
from tests.simulations_support import create_simulation_run, create_team_result

pytestmark = pytest.mark.integration

COMPLETE_SEASON_CODE = "2080-81"
"""The synthetic completed season: no data source covers 2080."""

COMPLETE_START_YEAR = 2080
"""The start year of that season, which also dates its own fixtures."""

PENDING_SEASON_CODE = "2081-82"
"""The synthetic unfinished season, which the requests without a code resolve."""

INCOMPLETE_SEASON_CODE = "2079-80"
"""The synthetic season whose fixtures are not all played."""

PARTIAL_SEASON_CODE = "2078-79"
"""The synthetic league of four clubs: finished, but too small to be counted."""

IDLE_SEASON_CODE = "2077-78"
"""The synthetic season of the not-found tests."""

CLUB_COUNT = 20
"""The league size the threshold counts, and the size of the synthetic season."""

RELEGATION_POSITIONS = 3
"""The configured relegation places of :class:`lmi_core.simulation.Qualification`."""

COUNTED_POSITION = CLUB_COUNT - RELEGATION_POSITIONS
"""The place just above the bottom three, which is 17 for twenty clubs."""

SEVENTEENTH_POINTS = 6 * (CLUB_COUNT - COUNTED_POSITION)
"""The complete season's total at that place: ``6 * (20 - 17) = 18`` points."""

SUBJECT_INDEX = 9
"""The club whose race the simulation tests serve, on ``6 * (19 - 9) = 60`` points."""

RIVAL_INDICES = (7, 8, 9, 10, 11)
"""The subject plus the four clubs nearest to it by expected position.

The subject is projected into tenth place, so the neighbours are ninth and
eleventh (distance one, ordered by position) and then eighth and twelfth
(distance two).
"""

PENDING_POSITIONS = (1, 2, 3, 4)
"""The places the unfinished season ranks its four clubs in: Alpha, Beta, subject, Gamma."""

HOME_OPPONENT_POSITION = 3
"""The place of Gamma, whose visit to the subject is the first remaining fixture."""

AWAY_OPPONENT_POSITION = 0
"""The place of Alpha, which hosts the subject in the second remaining fixture."""

PATH = "/v1/teams/{team_id}/relegation-race"
"""The path under test, formatted with a team identifier."""

RACE_REF = "#/components/schemas/RelegationRace"
PROBLEM_REF = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type every error response is sent with."""

RACE_FIELDS = {
    "team",
    "season",
    "run_id",
    "as_of_date",
    "survival_threshold",
    "rivals",
    "series",
    "remaining_fixtures",
    "next_match",
}
THRESHOLD_FIELDS = {"points_median", "points_min", "points_max", "seasons_used", "position"}
RIVAL_FIELDS = {
    "team",
    "current_points",
    "expected_points",
    "p_relegation",
    "points_p05",
    "points_p95",
}
FIXTURE_FIELDS = {"match", "venue", "opponent_position", "p_win", "p_draw", "p_loss"}
NEXT_MATCH_FIELDS = {"match_id", "outcomes", "current"}
CURRENT_FIELDS = {"p_relegation", "p_europe", "p_title", "expected_points"}
"""The field sets of the payload and its nested objects, so a silent addition is visible."""

N_RUNS = 1000
"""The sample size a synthetic run stores, which every outcome triple sums to."""

OUTCOME_COUNTS: Mapping[str, tuple[int, int, int, int, int]] = {
    "win": (450, 90, 120, 40, 23_400),
    "draw": (250, 100, 60, 0, 10_000),
    "loss": (300, 180, 40, 0, 9_600),
}
"""The stored counts of the subject's next match, as ``_store_outcome_counts`` reads them.

The three ``n_outcome`` sum to :data:`N_RUNS`, and the conditional counts sum to
the club's own row of the same run: 370 relegations, 220 European finishes and 40
titles out of 1000 samples, which is what ``_run_with_rows`` stores for the
subject of the unfinished season. So the swing has to average back to that row.
The point totals keep the same identity: 450 winning samples totalling 23 400
points, 250 drawing samples totalling 10 000 and 300 losing samples totalling
9 600 are the means 52, 40 and 32, and the three together total 43 000 points,
which is the club's stored ``mean_points`` of 43.0 times its 1000 samples.
"""

SMALL_RUN_N_RUNS = 500
"""The non-default sample size of the run the sample-size test stores."""

SMALL_RUN_OUTCOME_COUNTS: Mapping[str, tuple[int, int, int, int, int]] = {
    "win": (225, 45, 60, 20, 11_700),
    "draw": (125, 50, 30, 0, 5_000),
    "loss": (150, 90, 20, 0, 4_800),
}
"""The same club's next match, stored for a run of :data:`SMALL_RUN_N_RUNS` samples.

Every identity of :data:`OUTCOME_COUNTS` is kept at the smaller sample size: the
three ``n_outcome`` sum to 500, the relegation counts to 185 (0.37 times 500), the
European ones to 110 (0.22 times 500) and the title ones to 20 (0.04 times 500),
and the point totals to 21 500 (43 times 500). A read that divided by the default
1 000 would report every frequency at half its value.
"""


class World(NamedTuple):
    """One synthetic completed season with its clubs."""

    season: Season
    clubs: list[Team]


class Pending(NamedTuple):
    """The unfinished season, its four clubs and the subject's two remaining fixtures."""

    season: Season
    clubs: list[Team]
    subject: Team
    fixtures: list[Match]


class Small(NamedTuple):
    """The finished season of four clubs, which the threshold never counts."""

    season: Season
    clubs: list[Team]


def test_a_season_without_a_simulation_serves_the_threshold_and_the_fixtures(
    api_client: TestClient, db_session: Session
) -> None:
    """A race without a run is a 200: no run named, no rival, and no series point.

    The season is omitted on purpose, so the request resolves the unfinished
    season, the one that starts latest among the seasons with a match. The
    threshold is still read from the completed season, which is the whole point
    of the sample: the line does not come from the table on screen. The second
    fixture is inserted first, so the served order cannot be the insertion order.
    """
    world = _complete_season(db_session)
    pending = _pending_season(db_session, world)

    payload = _race(api_client, pending.subject.id)

    assert set(payload) == RACE_FIELDS
    assert payload["team"] == {"id": pending.subject.id, "name": pending.subject.name}
    assert payload["season"] == PENDING_SEASON_CODE
    assert payload["run_id"] is None
    assert payload["as_of_date"] is None
    assert payload["survival_threshold"] == {
        "points_median": float(SEVENTEENTH_POINTS),
        "points_min": SEVENTEENTH_POINTS,
        "points_max": SEVENTEENTH_POINTS,
        "seasons_used": 1,
        "position": COUNTED_POSITION,
    }
    assert payload["rivals"] == []
    assert payload["series"] == {"season": PENDING_SEASON_CODE, "points": []}
    assert payload["next_match"] is None

    fixtures = payload["remaining_fixtures"]
    assert [fixture["match"]["id"] for fixture in fixtures] == [
        fixture.id for fixture in pending.fixtures
    ]
    for fixture in fixtures:
        assert set(fixture) == FIXTURE_FIELDS
        assert fixture["p_win"] is None
        assert fixture["p_draw"] is None
        assert fixture["p_loss"] is None
    home, away = fixtures
    assert home["venue"] == "home"
    assert home["match"]["home_team"] == {"id": pending.subject.id, "name": pending.subject.name}
    assert home["opponent_position"] == PENDING_POSITIONS[HOME_OPPONENT_POSITION]
    assert away["venue"] == "away"
    assert away["match"]["away_team"] == {"id": pending.subject.id, "name": pending.subject.name}
    assert away["opponent_position"] == PENDING_POSITIONS[AWAY_OPPONENT_POSITION]


def test_the_threshold_counts_only_a_completed_twenty_club_season(
    api_client: TestClient, db_session: Session
) -> None:
    """An incomplete twenty-club season is left out of the sample.

    The second season has twenty clubs and a few results, so its seventeenth
    place holds 0 points. A read that counted it would report a sample of two and
    a median of 9.0, which is why the assertions below pin both the size and the
    median of the sample.
    """
    world = _complete_season(db_session)
    partial = create_season(
        db_session, code=INCOMPLETE_SEASON_CODE, start_year=COMPLETE_START_YEAR - 1
    )
    clubs = [
        create_team(db_session, name=f"Synthetic Partial FC {index:02d}")
        for index in range(CLUB_COUNT)
    ]
    for index in range(4):
        create_match(
            db_session,
            season=partial,
            home=clubs[index],
            away=clubs[(index + 1) % 4],
            kickoff_date=datetime.date(COMPLETE_START_YEAR - 1, 8, 1 + index),
            status="played",
            home_goals=1,
            away_goals=0,
        )

    payload = _race(api_client, world.clubs[SUBJECT_INDEX].id, season=COMPLETE_SEASON_CODE)

    assert payload["survival_threshold"] == {
        "points_median": float(SEVENTEENTH_POINTS),
        "points_min": SEVENTEENTH_POINTS,
        "points_max": SEVENTEENTH_POINTS,
        "seasons_used": 1,
        "position": COUNTED_POSITION,
    }


def test_a_league_too_small_to_count_has_no_completed_season(
    api_client: TestClient, db_session: Session
) -> None:
    """A finished season of four clubs is not a sample, so the field is not served.

    The field is not nullable by design: the endpoint refuses instead of falling
    back to the current table, which would read as a threshold somebody computed
    out of a season the rule does not accept.
    """
    small = _small_season(db_session)

    response = api_client.get(
        PATH.format(team_id=small.clubs[0].id), params={"season": small.season.code}
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["status"] == 404
    assert problem["detail"] == "no completed season"


def test_the_latest_succeeded_run_names_the_rivals_and_restricts_the_series(
    api_client: TestClient, db_session: Session
) -> None:
    """The run is the newest succeeded one, and the failed and repeated runs are not.

    Three succeeded runs are stored: one at an earlier as-of date and two at the
    same later date, of which the one that finished later must be selected. A
    failed run finishes last of all and must contribute nothing, neither to the
    named run nor to the series.
    """
    world = _complete_season(db_session)
    subject = world.clubs[SUBJECT_INDEX]
    older = _run_with_rows(
        db_session,
        season=world.season,
        clubs=world.clubs,
        as_of_date=datetime.date(COMPLETE_START_YEAR, 10, 1),
        requested_at=datetime.datetime(COMPLETE_START_YEAR, 10, 2, 9, 0, tzinfo=datetime.UTC),
        scale=0,
    )
    newer = _run_with_rows(
        db_session,
        season=world.season,
        clubs=world.clubs,
        as_of_date=datetime.date(COMPLETE_START_YEAR, 11, 1),
        requested_at=datetime.datetime(COMPLETE_START_YEAR, 11, 2, 9, 0, tzinfo=datetime.UTC),
        scale=0,
    )
    # Same as-of date as ``newer``, but it finished first, so that date belongs to
    # ``newer`` in the named run and in the series alike.
    _run_with_rows(
        db_session,
        season=world.season,
        clubs=world.clubs,
        as_of_date=datetime.date(COMPLETE_START_YEAR, 11, 1),
        requested_at=datetime.datetime(COMPLETE_START_YEAR, 11, 1, 9, 0, tzinfo=datetime.UTC),
        scale=1,
    )
    failed = _run_with_rows(
        db_session,
        season=world.season,
        clubs=world.clubs,
        as_of_date=datetime.date(COMPLETE_START_YEAR, 12, 1),
        requested_at=datetime.datetime(COMPLETE_START_YEAR, 12, 2, 9, 0, tzinfo=datetime.UTC),
        scale=2,
        status="failed",
    )

    payload = _race(api_client, subject.id, season=COMPLETE_SEASON_CODE)

    assert payload["run_id"] == str(newer.id)
    assert payload["as_of_date"] == "2080-11-01"

    expected_ids = [world.clubs[index].id for index in RIVAL_INDICES]
    rivals = payload["rivals"]
    assert [rival["team"]["id"] for rival in rivals] == expected_ids
    for rival, index in zip(rivals, RIVAL_INDICES, strict=True):
        assert set(rival) == RIVAL_FIELDS
        assert rival["team"]["name"] == world.clubs[index].name
        assert rival["current_points"] == 10 + index
        assert rival["expected_points"] == pytest.approx(40.0 + index)
        assert rival["p_relegation"] == pytest.approx(0.4 - index * 0.01)
        assert rival["points_p05"] == index
        assert rival["points_p95"] == 60 + index

    assert payload["series"]["season"] == COMPLETE_SEASON_CODE
    points = payload["series"]["points"]
    assert [point["as_of_date"] for point in points] == ["2080-10-01", "2080-11-01"]
    assert [point["run_id"] for point in points] == [str(older.id), str(newer.id)]
    assert str(failed.id) not in {point["run_id"] for point in points}
    for point in points:
        assert [team["team"]["id"] for team in point["teams"]] == expected_ids
    # The later point is the row of the run that finished after the other run of
    # the same date, whose numbers were written one scale higher.
    assert points[-1]["teams"][0]["expected_points"] == pytest.approx(40.0 + RIVAL_INDICES[0])


def test_a_run_without_a_row_for_the_club_serves_no_rivals(
    api_client: TestClient, db_session: Session
) -> None:
    """The run is still named when the club is absent from its table.

    The run exists and holds rows for other clubs, so the answer names it; the
    group and its series are empty because there is no club whose race could be
    read out of that run. Substituting another run would report a simulation the
    response does not name.
    """
    world = _complete_season(db_session)
    subject = world.clubs[CLUB_COUNT - 1]
    run = _run_with_rows(
        db_session,
        season=world.season,
        clubs=world.clubs[:5],
        as_of_date=datetime.date(COMPLETE_START_YEAR, 10, 1),
        requested_at=datetime.datetime(COMPLETE_START_YEAR, 10, 2, 9, 0, tzinfo=datetime.UTC),
        scale=0,
    )

    payload = _race(api_client, subject.id, season=COMPLETE_SEASON_CODE)

    assert payload["run_id"] == str(run.id)
    assert payload["as_of_date"] == "2080-10-01"
    assert payload["rivals"] == []
    assert payload["series"] == {"season": COMPLETE_SEASON_CODE, "points": []}
    assert payload["survival_threshold"]["seasons_used"] == 1


def test_a_run_without_a_row_for_the_club_still_reads_both_detail_statements(
    api_client: TestClient, db_session: Session
) -> None:
    """The club's absence from the run is decided after both detail statements.

    The run of the unfinished season holds rows for the other three clubs and
    stored counts for the subject's next fixture, but no row for the subject
    itself, so ``current`` does not exist and the block stays null. The two
    conditional detail reads are issued all the same, because the conditions
    that gate them are the succeeded run and the remaining fixture: the fixture
    triple is still served, and the counts read is not skipped just because the
    run has nothing to hang the swing on.
    """
    world = _complete_season(db_session)
    pending = _pending_season(db_session, world)
    others = [club for club in pending.clubs if club.id != pending.subject.id]
    run = _run_with_rows(
        db_session,
        season=pending.season,
        clubs=others,
        as_of_date=datetime.date(COMPLETE_START_YEAR + 1, 10, 1),
        requested_at=datetime.datetime(COMPLETE_START_YEAR + 1, 10, 2, 9, 0, tzinfo=datetime.UTC),
        scale=0,
    )
    home, _away = pending.fixtures
    _store_fixture(db_session, run=run, match=home, p_home=0.5, p_draw=0.25, p_away=0.25)
    _store_outcome_counts(
        db_session, run=run, team=pending.subject, match=home, counts=OUTCOME_COUNTS
    )

    with _counted_statements(db_session) as statements:
        payload = _race(api_client, pending.subject.id, season=PENDING_SEASON_CODE)

    assert payload["run_id"] == str(run.id)
    assert payload["next_match"] is None
    assert payload["rivals"] == []
    home_fixture, away_fixture = payload["remaining_fixtures"]
    assert (home_fixture["p_win"], home_fixture["p_draw"], home_fixture["p_loss"]) == (
        0.5,
        0.25,
        0.25,
    )
    assert away_fixture["p_win"] is None
    # Nine statements: the eleven of the full world without the two history
    # reads, which an empty rival group skips. Both detail reads are among them,
    # so a read that skipped the counts one would stop at eight.
    assert len(statements) == 9
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_an_unknown_team_id_is_not_found_without_echoing_the_value(
    api_client: TestClient, db_session: Session
) -> None:
    small = _small_season(db_session)
    unknown = 2_147_483_647

    response = api_client.get(PATH.format(team_id=unknown), params={"season": small.season.code})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = response.json()
    assert problem["title"] == "Not Found"
    assert problem["status"] == 404
    assert problem["detail"] == "Team not found"
    assert problem["errors"] is None
    assert str(unknown) not in response.text


def test_a_club_outside_the_season_is_not_found_in_the_season(
    api_client: TestClient, db_session: Session
) -> None:
    """A stored club that is no side of the season is reported before the sample.

    The season holds four clubs and is therefore not a counted one. The
    membership check comes first, so the client is told that its club is not part
    of the season rather than that the league has no completed season, which would
    send it looking for a different problem.
    """
    small = _small_season(db_session)
    outsider = create_team(db_session, name="Synthetic Outsider FC")

    response = api_client.get(
        PATH.format(team_id=outsider.id), params={"season": small.season.code}
    )

    assert response.status_code == 404
    problem = response.json()
    assert problem["detail"] == "Team not found in season"
    assert str(outsider.id) not in response.text


def test_an_unknown_season_code_is_not_found(api_client: TestClient, db_session: Session) -> None:
    club = create_team(db_session, name="Synthetic Unknown Season FC")
    unknown = "2069-70"

    response = api_client.get(PATH.format(team_id=club.id), params={"season": unknown})

    assert response.status_code == 404
    problem = response.json()
    assert problem["detail"] == "Season not found"
    assert unknown not in response.text


def test_the_fixture_probabilities_are_read_from_the_clubs_side_of_each_venue(
    api_client: TestClient, db_session: Session
) -> None:
    """The stored triple describes the two clubs, so the club's side decides the mapping.

    The first fixture is the subject's home match and the second one its away
    match, and the two rows store visibly different numbers, so a read that
    served ``p_home`` as ``p_win`` at both venues would show it. The draw is the
    draw at either venue, which is why only the win and the loss swap.
    """
    pending, run = _pending_with_run(db_session)
    home, away = pending.fixtures
    _store_fixture(db_session, run=run, match=home, p_home=0.5, p_draw=0.25, p_away=0.25)
    _store_fixture(db_session, run=run, match=away, p_home=0.6, p_draw=0.15, p_away=0.25)

    fixtures = _race(api_client, pending.subject.id, season=PENDING_SEASON_CODE)[
        "remaining_fixtures"
    ]

    assert fixtures[0]["venue"] == "home"
    assert (fixtures[0]["p_win"], fixtures[0]["p_draw"], fixtures[0]["p_loss"]) == (
        0.5,
        0.25,
        0.25,
    )
    assert fixtures[1]["venue"] == "away"
    assert (fixtures[1]["p_win"], fixtures[1]["p_draw"], fixtures[1]["p_loss"]) == (
        0.25,
        0.15,
        0.6,
    )


def test_a_fixture_the_run_did_not_simulate_stays_null(
    api_client: TestClient, db_session: Session
) -> None:
    """One stored fixture is served and the other keeps three nulls.

    A run that simulated only part of the remaining calendar is a legitimate
    run, so the fixture it skipped is answered as unknown rather than with a
    probability borrowed from the fixture beside it.
    """
    pending, run = _pending_with_run(db_session)
    home, _away = pending.fixtures
    _store_fixture(db_session, run=run, match=home, p_home=0.5, p_draw=0.25, p_away=0.25)

    fixtures = _race(api_client, pending.subject.id, season=PENDING_SEASON_CODE)[
        "remaining_fixtures"
    ]

    assert fixtures[0]["p_win"] == 0.5
    assert fixtures[0]["p_loss"] == 0.25
    assert fixtures[1]["p_win"] is None
    assert fixtures[1]["p_draw"] is None
    assert fixtures[1]["p_loss"] is None


def test_a_run_without_detail_rows_serves_a_null_next_match(
    api_client: TestClient, db_session: Session
) -> None:
    """A run created before the detail tables existed is still a served race.

    The run is named and its table fills the rivals and the series, while the
    per-fixture triple and the next-match block are null with HTTP 200: the
    detail is absent from that run, which is a state of the data and not an
    error of the request.
    """
    pending, run = _pending_with_run(db_session)

    payload = _race(api_client, pending.subject.id, season=PENDING_SEASON_CODE)

    assert payload["run_id"] == str(run.id)
    assert payload["rivals"]
    assert payload["next_match"] is None
    for fixture in payload["remaining_fixtures"]:
        assert fixture["p_win"] is None
        assert fixture["p_draw"] is None
        assert fixture["p_loss"] is None


def test_a_stored_next_match_that_is_no_longer_next_serves_a_null_next_match(
    api_client: TestClient, db_session: Session
) -> None:
    """Counts for the fixture after the next one are stale, so the block stays unknown.

    The run stored its counts for the second remaining fixture, which is what a
    run created before the first match was played looks like once that match is
    gone: the stored match is no longer the club's next one, and serving its
    conditional probabilities would describe a match the club no longer plays.
    """
    pending, run = _pending_with_run(db_session)
    _home, away = pending.fixtures
    _store_outcome_counts(
        db_session, run=run, team=pending.subject, match=away, counts=OUTCOME_COUNTS
    )

    payload = _race(api_client, pending.subject.id, season=PENDING_SEASON_CODE)

    assert payload["next_match"] is None


def test_a_never_sampled_outcome_serves_a_null_next_match(
    api_client: TestClient, db_session: Session
) -> None:
    """A result no simulated run produced has no conditional probability.

    The draw was never sampled, so dividing its conditional counts by a zero
    sample would invent a probability. The three outcomes complete the run -- the
    counts still sum to the sample size -- and the block is still unknown.
    """
    pending, run = _pending_with_run(db_session)
    home, _away = pending.fixtures
    _store_outcome_counts(
        db_session,
        run=run,
        team=pending.subject,
        match=home,
        counts={
            "win": (450, 90, 120, 40, 1450),
            "draw": (0, 0, 0, 0, 0),
            "loss": (550, 280, 100, 0, 550),
        },
    )

    payload = _race(api_client, pending.subject.id, season=PENDING_SEASON_CODE)

    assert payload["next_match"] is None


def test_the_swing_averages_back_to_the_clubs_own_row(
    api_client: TestClient, db_session: Session
) -> None:
    """Each stored outcome weights its own values back to the club's probabilities.

    ``p_outcome`` is the sampled frequency, so the three outcomes together must
    reproduce the run's own relegation, Europe and title probabilities. The
    relegation one is read from the club's rival row and the other two from its
    team row of the series, because the rival contract carries only the first.
    Each outcome is weighed against its own values, never against a separate sum
    of the probabilities. The stored point totals weigh back the same way, and
    their weighted mean is the ``expected_points`` of the club's own row, which
    the block serves as ``current``.
    """
    pending, run = _pending_with_run(db_session)
    home, _away = pending.fixtures
    _store_outcome_counts(
        db_session, run=run, team=pending.subject, match=home, counts=OUTCOME_COUNTS
    )

    payload = _race(api_client, pending.subject.id, season=PENDING_SEASON_CODE)

    outcomes = payload["next_match"]["outcomes"]
    assert sum(outcome["p_outcome"] for outcome in outcomes.values()) == pytest.approx(1.0)
    rival = next(row for row in payload["rivals"] if row["team"]["id"] == pending.subject.id)
    team_row = next(
        row
        for point in payload["series"]["points"]
        for row in point["teams"]
        if row["team"]["id"] == pending.subject.id
    )
    relegation = sum(
        outcome["p_outcome"] * outcome["p_relegation"] for outcome in outcomes.values()
    )
    europe = sum(outcome["p_outcome"] * outcome["p_europe"] for outcome in outcomes.values())
    title = sum(outcome["p_outcome"] * outcome["p_title"] for outcome in outcomes.values())

    assert abs(relegation - rival["p_relegation"]) < 1e-9
    assert abs(europe - team_row["p_europe"]) < 1e-9
    assert abs(title - team_row["p_title"]) < 1e-9
    points = sum(outcome["p_outcome"] * outcome["expected_points"] for outcome in outcomes.values())
    assert points == pytest.approx(payload["next_match"]["current"]["expected_points"])


def test_a_run_is_read_at_its_own_sample_size(api_client: TestClient, db_session: Session) -> None:
    """Every frequency divides by the run's ``n_runs``, never by the default one.

    The run stores :data:`SMALL_RUN_N_RUNS` samples, so a read that divided the
    stored counts by the default 1 000 would halve all three frequencies, and a
    read that assumed the default would also reject the counts as an incomplete
    run. The stored numbers keep every identity of the fixture at that size: the
    counts sum to the run's sample size, the conditional counts to the club's row
    scaled to it, and the point totals to its mean points times it.
    """
    world = _complete_season(db_session)
    pending = _pending_season(db_session, world)
    run = _run_with_rows(
        db_session,
        season=pending.season,
        clubs=pending.clubs,
        as_of_date=datetime.date(COMPLETE_START_YEAR + 1, 10, 1),
        requested_at=datetime.datetime(COMPLETE_START_YEAR + 1, 10, 2, 9, 0, tzinfo=datetime.UTC),
        scale=0,
        n_runs=SMALL_RUN_N_RUNS,
    )
    home, _away = pending.fixtures
    _store_outcome_counts(
        db_session,
        run=run,
        team=pending.subject,
        match=home,
        counts=SMALL_RUN_OUTCOME_COUNTS,
    )

    payload = _race(api_client, pending.subject.id, season=PENDING_SEASON_CODE)

    outcomes = payload["next_match"]["outcomes"]
    for outcome, counts in SMALL_RUN_OUTCOME_COUNTS.items():
        assert outcomes[outcome]["p_outcome"] == pytest.approx(counts[0] / SMALL_RUN_N_RUNS)
    assert sum(outcome["p_outcome"] for outcome in outcomes.values()) == pytest.approx(1.0)
    rival = next(row for row in payload["rivals"] if row["team"]["id"] == pending.subject.id)
    team_row = next(
        row
        for point in payload["series"]["points"]
        for row in point["teams"]
        if row["team"]["id"] == pending.subject.id
    )
    relegation = sum(
        outcome["p_outcome"] * outcome["p_relegation"] for outcome in outcomes.values()
    )
    europe = sum(outcome["p_outcome"] * outcome["p_europe"] for outcome in outcomes.values())
    title = sum(outcome["p_outcome"] * outcome["p_title"] for outcome in outcomes.values())

    assert abs(relegation - rival["p_relegation"]) < 1e-9
    assert abs(europe - team_row["p_europe"]) < 1e-9
    assert abs(title - team_row["p_title"]) < 1e-9


def test_the_next_match_current_is_the_clubs_own_row_of_the_same_run(
    api_client: TestClient, db_session: Session
) -> None:
    """The baseline of the swing is the club's team row, not a second run.

    The expected numbers are the ones ``_run_with_rows`` stores for the subject
    of the unfinished season: index three on scale zero is 0.37 relegation,
    0.22 Europe, 0.04 title and forty-three expected points. Serving a different
    run's row, or a rival's, would show it.
    """
    pending, run = _pending_with_run(db_session)
    home, _away = pending.fixtures
    _store_outcome_counts(
        db_session, run=run, team=pending.subject, match=home, counts=OUTCOME_COUNTS
    )

    payload = _race(api_client, pending.subject.id, season=PENDING_SEASON_CODE)

    current = payload["next_match"]["current"]
    assert set(current) == CURRENT_FIELDS
    assert current == pytest.approx(
        {"p_relegation": 0.37, "p_europe": 0.22, "p_title": 0.04, "expected_points": 43.0}
    )


def test_the_read_issues_a_fixed_number_of_statements(
    api_client: TestClient, db_session: Session
) -> None:
    """The read is bounded by its eleven statements, never by the payload size.

    The world holds a completed season of 380 results and a second season of four
    clubs with four matches and two remaining fixtures, plus a succeeded run with
    four team rows, so a read that loaded one season, one club or one fixture at a
    time would need more statements than this. The two detail reads count towards
    the total because the run succeeded and the club has a remaining fixture. The
    count is taken on the connection the fixture session uses, so it observes what
    the request really issued and not what the code claims.
    """
    world = _complete_season(db_session)
    pending = _pending_season(db_session, world)
    _run_with_rows(
        db_session,
        season=pending.season,
        clubs=pending.clubs,
        as_of_date=datetime.date(COMPLETE_START_YEAR + 1, 10, 1),
        requested_at=datetime.datetime(COMPLETE_START_YEAR + 1, 10, 2, 9, 0, tzinfo=datetime.UTC),
        scale=0,
    )

    with _counted_statements(db_session) as statements:
        payload = _race(api_client, pending.subject.id, season=PENDING_SEASON_CODE)

    assert payload["rivals"]
    assert payload["remaining_fixtures"]
    assert len(statements) == 11
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_the_read_without_a_run_or_a_remaining_fixture_stops_after_six(
    api_client: TestClient, db_session: Session
) -> None:
    """A race with nothing left to play and no simulation skips every conditional read.

    The completed season holds a match, so the club, the sample and the table are
    read; without a succeeded run there are no rivals, hence no history, and
    without a remaining fixture there is nothing to summarise or to detail. The
    path therefore stops after six statements.
    """
    world = _complete_season(db_session)

    with _counted_statements(db_session) as statements:
        payload = _race(api_client, world.clubs[SUBJECT_INDEX].id, season=COMPLETE_SEASON_CODE)

    assert payload["remaining_fixtures"] == []
    assert payload["next_match"] is None
    assert len(statements) == 6
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_openapi_documents_the_relegation_race_contract() -> None:
    document = create_app().openapi()
    operation = document["paths"]["/v1/teams/{team_id}/relegation-race"]["get"]

    assert operation["operationId"] == "get_team_relegation_race"
    assert operation["tags"] == ["teams"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": RACE_REF
    }
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert set(parameters) == {"team_id", "season"}
    for status in ("404", "422"):
        content = operation["responses"][status]["content"]
        assert PROBLEM_MEDIA_TYPE in content
        assert any(media.get("schema") == {"$ref": PROBLEM_REF} for media in content.values())

    schemas = document["components"]["schemas"]
    for name in (
        "RelegationRace",
        "SurvivalThreshold",
        "RelegationRival",
        "RemainingFixture",
        "NextMatch",
        "NextMatchCurrent",
        "NextMatchOutcomes",
        "NextMatchOutcome",
        "SimulationHistory",
    ):
        assert name in schemas
    assert set(schemas["RelegationRace"]["properties"]) == RACE_FIELDS
    assert set(schemas["SurvivalThreshold"]["properties"]) == THRESHOLD_FIELDS
    assert set(schemas["RelegationRival"]["properties"]) == RIVAL_FIELDS
    assert set(schemas["RemainingFixture"]["properties"]) == FIXTURE_FIELDS
    assert set(schemas["NextMatch"]["properties"]) == NEXT_MATCH_FIELDS
    assert set(schemas["NextMatchCurrent"]["properties"]) == CURRENT_FIELDS


def test_the_simulation_outputs_are_declared_nullable() -> None:
    """The run's outputs are nullable, so a run that stored none serves null.

    ``next_match`` and the three per-fixture probabilities are nullable in the
    published contract rather than absent from it, which is what lets a run
    created before revision 0004, or a fixture the run did not simulate, be
    served as unknown instead of as a number nobody measured.
    """
    schemas = create_app().openapi()["components"]["schemas"]
    race = schemas["RelegationRace"]["properties"]
    remaining = schemas["RemainingFixture"]["properties"]

    assert _accepts_null(race["next_match"])
    for field in ("p_win", "p_draw", "p_loss"):
        assert _accepts_null(remaining[field])


def _accepts_null(schema: dict[str, Any]) -> bool:
    """Return whether ``schema`` admits null, whatever shape it renders in."""
    if schema.get("type") == "null" or schema.get("nullable") is True:
        return True
    return any(_accepts_null(entry) for entry in schema.get("anyOf", []))


def _race(api_client: TestClient, team_id: int, *, season: str | None = None) -> dict[str, Any]:
    """Return the payload of one successful relegation-race request."""
    params = {} if season is None else {"season": season}
    response = api_client.get(PATH.format(team_id=team_id), params=params)
    assert response.status_code == 200, response.text
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


def _complete_season(
    session: Session,
    *,
    code: str = COMPLETE_SEASON_CODE,
    start_year: int = COMPLETE_START_YEAR,
) -> World:
    """Store a completed twenty-club double round robin and return its rows.

    The club at index ``i`` wins both legs against every club with a higher index
    and loses both against every lower one, 1-0 each time. It therefore wins
    ``2 * (19 - i)`` matches, loses ``2 * i`` and finishes on ``6 * (19 - i)``
    points, so every place holds a distinct total and the table is the index
    order. The one expected value the tests need, the seventeenth place's total,
    follows from that arithmetic alone.
    """
    season = create_season(session, code=code, start_year=start_year)
    clubs = [
        create_team(session, name=f"Synthetic Relegation FC {index:02d}")
        for index in range(CLUB_COUNT)
    ]
    for home_index, home in enumerate(clubs):
        for away_index, away in enumerate(clubs):
            if home_index == away_index:
                continue
            home_wins = home_index < away_index
            create_match(
                session,
                season=season,
                home=home,
                away=away,
                kickoff_date=datetime.date(start_year, 8, 1) + datetime.timedelta(days=home_index),
                matchday=home_index + 1,
                status="played",
                home_goals=1 if home_wins else 0,
                away_goals=0 if home_wins else 1,
            )
    return World(season=season, clubs=clubs)


def _pending_season(session: Session, world: World) -> Pending:
    """Store the unfinished season of four clubs and return it with its fixtures.

    Alpha beats Beta 2-0 and Beta beats Gamma 1-0, so Alpha leads on a head-to-head
    win over Beta at three points, Beta follows at three, and the subject sits
    above Gamma on goal difference at no points. The subject's second fixture is
    inserted first, so the served order cannot be the insertion order.
    """
    alpha, beta, gamma, subject = world.clubs[0], world.clubs[1], world.clubs[2], world.clubs[3]
    start_year = COMPLETE_START_YEAR + 1
    season = create_season(session, code=PENDING_SEASON_CODE, start_year=start_year)
    create_match(
        session,
        season=season,
        home=alpha,
        away=beta,
        kickoff_date=datetime.date(start_year, 8, 1),
        status="played",
        home_goals=2,
        away_goals=0,
    )
    create_match(
        session,
        season=season,
        home=beta,
        away=gamma,
        kickoff_date=datetime.date(start_year, 8, 8),
        status="played",
        home_goals=1,
        away_goals=0,
    )
    away = create_match(
        session,
        season=season,
        home=alpha,
        away=subject,
        kickoff_date=datetime.date(start_year, 12, 5),
        matchday=22,
    )
    home = create_match(
        session,
        season=season,
        home=subject,
        away=gamma,
        kickoff_date=datetime.date(start_year, 12, 1),
        matchday=21,
    )
    return Pending(
        season=season,
        clubs=[alpha, beta, gamma, subject],
        subject=subject,
        fixtures=[home, away],
    )


def _small_season(session: Session) -> Small:
    """Store a small finished season and return it with its four clubs.

    Four clubs and twelve results, so the season is complete but too small to be
    counted by the threshold. The not-found tests reuse it because they never
    reach the sample: the club, the season and the membership are resolved first.
    """
    start_year = COMPLETE_START_YEAR - 2
    season = create_season(session, code=PARTIAL_SEASON_CODE, start_year=start_year)
    clubs = [create_team(session, name=f"Synthetic Small FC {index}") for index in range(4)]
    for home_index, home in enumerate(clubs):
        for away_index, away in enumerate(clubs):
            if home_index == away_index:
                continue
            create_match(
                session,
                season=season,
                home=home,
                away=away,
                kickoff_date=datetime.date(start_year, 8, 1 + home_index),
                status="played",
                home_goals=1 if home_index < away_index else 0,
                away_goals=0 if home_index < away_index else 1,
            )
    return Small(season=season, clubs=clubs)


def _run_with_rows(
    session: Session,
    *,
    season: Season,
    clubs: Sequence[Team],
    as_of_date: datetime.date,
    requested_at: datetime.datetime,
    scale: int,
    status: str = "succeeded",
    n_runs: int = N_RUNS,
) -> SimulationRun:
    """Store one run with a row per club and return it.

    The stored numbers are a function of the club's index and of ``scale``, so a
    read that selected the wrong run serves visibly different numbers instead of
    the same ones. ``scale`` is a parameter and not a counter, because a counter
    shared between tests would make the expected numbers depend on test order.
    ``n_runs`` is passed through to the run, so a test can state a sample size
    other than the default one and pin that the swing divides by it.
    """
    offset = 100 * scale
    run = create_simulation_run(
        session,
        season=season,
        status=status,
        as_of_date=as_of_date,
        requested_at=requested_at,
        n_runs=n_runs,
    )
    for index, club in enumerate(clubs):
        create_team_result(
            session,
            run=run,
            team=club,
            mean_position=float(index + 1),
            mean_points=40.0 + index + offset,
            current_points=10 + index,
            p_relegation=0.4 - index * 0.01,
            p_europe=0.25 - index * 0.01 + 0.01 * scale,
            p_title=0.01 * (index + 1) + 0.01 * scale,
            points_p05=index + offset,
            points_p95=60 + index + offset,
        )
    return run


def _pending_with_run(session: Session) -> tuple[Pending, SimulationRun]:
    """Store the completed season, the unfinished one and a succeeded run of it.

    The completed season is what the threshold counts, and the run of the
    unfinished season is the one the detail reads select, so this is the world
    every detail test starts from. The run is written at scale zero, which is the
    scale the stored numbers of those tests are stated at.
    """
    world = _complete_season(session)
    pending = _pending_season(session, world)
    run = _run_with_rows(
        session,
        season=pending.season,
        clubs=pending.clubs,
        as_of_date=datetime.date(COMPLETE_START_YEAR + 1, 10, 1),
        requested_at=datetime.datetime(COMPLETE_START_YEAR + 1, 10, 2, 9, 0, tzinfo=datetime.UTC),
        scale=0,
    )
    return pending, run


def _store_fixture(
    session: Session,
    *,
    run: SimulationRun,
    match: Match,
    p_home: float,
    p_draw: float,
    p_away: float,
) -> None:
    """Store one simulated fixture of ``run`` and flush, never commit.

    The triple describes the two clubs of the match, home side first, which is
    what lets a test pin the venue mapping: the club's own win probability is
    ``p_home`` at home and ``p_away`` away.
    """
    session.add(
        SimulationFixture(
            run_id=run.id, match_id=match.id, p_home=p_home, p_draw=p_draw, p_away=p_away
        )
    )
    session.flush()


def _store_outcome_counts(
    session: Session,
    *,
    run: SimulationRun,
    team: Team,
    match: Match,
    counts: Mapping[str, tuple[int, int, int, int, int]],
) -> None:
    """Store one row per outcome of ``team``'s next match in ``run`` and flush.

    ``counts`` maps each outcome to its ``n_outcome``, its three conditional
    counts and its ``points_sum``, so a test states the stored numbers the rule
    is expected to read. Nothing is committed: the caller's transaction decides
    whether the rows survive.
    """
    for outcome, (n_outcome, n_relegated, n_europe, n_title, points_sum) in counts.items():
        session.add(
            SimulationNextMatch(
                run_id=run.id,
                team_id=team.id,
                match_id=match.id,
                outcome=outcome,
                n_outcome=n_outcome,
                n_relegated=n_relegated,
                n_europe=n_europe,
                n_title=n_title,
                points_sum=points_sum,
            )
        )
    session.flush()
