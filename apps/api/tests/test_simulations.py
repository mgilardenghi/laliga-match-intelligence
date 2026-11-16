"""Contract tests for the ``/v1/simulations`` endpoints on a disposable database.

The suite runs against a database that exists only for the run, so a test can
assert on the absolute number of stored runs whenever it knows it wrote every
one of them. Every row it writes comes from :mod:`tests.simulations_support`,
which invents the club names and the season codes and inserts the runs directly:
the endpoints only read what the worker stored, so no test executes a simulation.

The client fixture replaces the queue dependency with
:class:`~tests.simulations_support.FakeQueue`, so the create endpoint is
exercised end to end without Redis and without a worker. The assertions are on
the payloads the API returns and on the rows it stored, never on the source of
the endpoints.
"""

import datetime
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from lmi_api.db import get_session
from lmi_api.main import create_app
from lmi_api.queue import get_queue
from lmi_core.clock import madrid_today
from lmi_core.db.models import SimulationRun, SimulationTeamResult
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.factories import create_match, create_season
from tests.simulations_support import (
    FakeQueue,
    create_simulation_run,
    create_simulation_season,
    create_team_result,
)

pytestmark = pytest.mark.integration

PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type every error response is sent with."""

RUN_FIELDS = {
    "id",
    "season",
    "as_of_date",
    "n_runs",
    "seed",
    "model",
    "model_version",
    "status",
    "kind",
    "trigger",
    "requested_at",
    "started_at",
    "finished_at",
    "error",
    "n_fixed",
    "n_simulated",
    "last_complete_matchday",
    "qualification",
}
"""Every field a simulation run payload carries."""

TEAM_ROW_FIELDS = {
    "team",
    "current",
    "expected_points",
    "points_quantiles",
    "points_distribution",
    "expected_position",
    "position_probabilities",
    "p_title",
    "p_ucl",
    "p_europe",
    "p_relegation",
}
"""Every field one row of a projected table carries."""

HISTORY_TEAM_FIELDS = {
    "team",
    "p_title",
    "p_ucl",
    "p_europe",
    "p_relegation",
    "expected_points",
    "expected_position",
}
"""Every field one team of a history point carries."""

HISTORY_POINT_FIELDS = {"run_id", "as_of_date", "kind", "last_complete_matchday", "teams"}
"""Every field one history point carries."""

ABSENT_TEAM_ID = 2_000_000_000
"""A team identifier no row has, used for the unknown-team 404."""


@pytest.fixture
def fake_queue() -> FakeQueue:
    """Yield the queue fake the create endpoint will be given."""
    return FakeQueue()


@pytest.fixture
def api_client(db_session: Session, fake_queue: FakeQueue) -> Iterator[TestClient]:
    """Yield a client whose session is ``db_session`` and whose queue is the fake.

    It is the shared client fixture with one more override: the producer
    dependency is replaced by the fake, so a create request records its
    identifiers in ``fake_queue`` instead of talking to Redis.
    """
    application = create_app()
    application.dependency_overrides[get_session] = lambda: db_session
    application.dependency_overrides[get_queue] = lambda: fake_queue
    try:
        with TestClient(application) as client:
            yield client
    finally:
        application.dependency_overrides.clear()


def assert_run_payload(item: dict[str, Any], run: SimulationRun, season_code: str) -> None:
    """Assert one payload row carries the stored value of every run field.

    ``item`` is the decoded JSON object, so the identifier is a string, the
    timestamps are ISO 8601 strings and the date is ``YYYY-MM-DD``. The
    timestamps are parsed back to aware datetimes before they are compared, which
    is what proves the API does not shift the instants it stored.
    """
    assert set(item) == RUN_FIELDS
    assert item["id"] == str(run.id)
    assert item["season"] == season_code
    assert item["as_of_date"] == run.as_of_date.isoformat()
    assert item["n_runs"] == run.n_runs
    assert item["seed"] == run.seed
    assert item["model"] == run.model
    assert item["model_version"] == run.model_version
    assert item["status"] == run.status
    assert item["kind"] == run.kind
    assert item["trigger"] == run.trigger
    assert _as_datetime(item["requested_at"]) == run.requested_at
    assert _as_optional_datetime(item["started_at"]) == run.started_at
    assert _as_optional_datetime(item["finished_at"]) == run.finished_at
    assert item["error"] == run.error
    assert item["n_fixed"] == run.n_fixed
    assert item["n_simulated"] == run.n_simulated
    assert item["last_complete_matchday"] == run.last_complete_matchday
    assert item["qualification"] == {
        "ucl_positions": run.ucl_positions,
        "europe_positions": run.europe_positions,
        "relegation_positions": run.relegation_positions,
    }


def _as_datetime(value: Any) -> datetime.datetime:
    """Return a serialised timestamp as the datetime it spells."""
    assert isinstance(value, str), f"the payload timestamp is not a string: {value!r}"
    return datetime.datetime.fromisoformat(value)


def _as_optional_datetime(value: Any) -> datetime.datetime | None:
    """Return a serialised timestamp, or ``None`` when the payload carries null."""
    return None if value is None else _as_datetime(value)


def _ids(items: list[Any]) -> list[str]:
    """Return the identifiers of a payload row list, in the order it arrived."""
    return [item["id"] for item in items]


def test_create_simulation_stores_a_queued_run_and_enqueues_it(
    api_client: TestClient, db_session: Session, fake_queue: FakeQueue
) -> None:
    season, _ = create_simulation_season(db_session)
    yesterday = madrid_today() - datetime.timedelta(days=1)

    response = api_client.post(
        "/v1/simulations",
        json={
            "season": season.code,
            "as_of_date": yesterday.isoformat(),
            "n_runs": 2000,
            "seed": 7,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["season"] == season.code
    assert body["as_of_date"] == yesterday.isoformat()
    assert body["n_runs"] == 2000
    assert body["seed"] == 7
    assert body["model"] == "dixon_coles"
    assert body["kind"] == "live"
    assert body["trigger"] == "api"
    # A queued run has observed nothing yet, so every worker column is null.
    assert body["started_at"] is None
    assert body["finished_at"] is None
    assert body["model_version"] is None
    assert body["n_fixed"] is None
    assert body["n_simulated"] is None
    assert body["last_complete_matchday"] is None
    assert body["error"] is None
    assert response.headers["location"] == f"/v1/simulations/{body['id']}"
    assert fake_queue.attempts == [uuid.UUID(body["id"])]

    stored = db_session.get(SimulationRun, uuid.UUID(body["id"]))
    assert stored is not None
    assert stored.season_id == season.id
    assert stored.trigger == "api"
    assert stored.kind == "live"
    assert stored.status == "queued"
    assert_run_payload(body, stored, season.code)


def test_create_simulation_returns_the_same_run_for_an_identical_request(
    api_client: TestClient, db_session: Session, fake_queue: FakeQueue
) -> None:
    season, _ = create_simulation_season(db_session)
    body = {"season": season.code, "n_runs": 1500, "seed": 3}

    first = api_client.post("/v1/simulations", json=body)
    second = api_client.post("/v1/simulations", json=body)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json() == first.json()
    assert second.headers["location"] == first.headers["location"]
    # The active row holds the natural key, so the second request added no run
    # and sent no second message.
    assert fake_queue.attempts == [uuid.UUID(first.json()["id"])]
    assert db_session.scalar(select(func.count()).select_from(SimulationRun)) == 1


def test_create_simulation_marks_the_run_failed_and_answers_503(
    api_client: TestClient, db_session: Session, fake_queue: FakeQueue
) -> None:
    season, _ = create_simulation_season(db_session)
    fake_queue.failure = RuntimeError("the broker is unreachable")

    response = api_client.post("/v1/simulations", json={"season": season.code, "n_runs": 1200})

    assert response.status_code == 503
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    problem = response.json()
    assert problem["status"] == 503
    assert problem["detail"] == "Simulation enqueue failed"

    db_session.expire_all()
    assert len(fake_queue.attempts) == 1
    stored = db_session.get(SimulationRun, fake_queue.attempts[0])
    assert stored is not None
    assert stored.status == "failed"
    assert stored.finished_at is not None
    assert stored.started_at is not None
    assert stored.error is not None
    # The broker's own text stays in the log: the stored column is readable
    # through the API and must not become a second way to disclose it.
    assert "broker" not in stored.error


def test_create_simulation_defaults_to_the_season_with_a_match(
    api_client: TestClient, db_session: Session, fake_queue: FakeQueue
) -> None:
    # A later season without a single match must not win: a request there would
    # simulate an empty league.
    create_season(db_session, code="2099-00", start_year=2099)
    season, teams = create_simulation_season(db_session)
    create_match(db_session, season=season, home=teams[0], away=teams[1])

    response = api_client.post("/v1/simulations", json={})

    assert response.status_code == 202
    body = response.json()
    assert body["season"] == season.code
    assert body["as_of_date"] == madrid_today().isoformat()
    assert body["n_runs"] == 10000
    assert body["seed"] == 0
    assert fake_queue.attempts == [uuid.UUID(body["id"])]


def test_create_simulation_answers_404_for_an_unknown_season(
    api_client: TestClient, db_session: Session, fake_queue: FakeQueue
) -> None:
    create_simulation_season(db_session)

    response = api_client.post("/v1/simulations", json={"season": "3000-01"})

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["detail"] == "Season not found"
    assert fake_queue.attempts == []
    assert db_session.scalar(select(func.count()).select_from(SimulationRun)) == 0


def test_create_simulation_answers_404_when_no_season_has_a_match(
    api_client: TestClient, fake_queue: FakeQueue
) -> None:
    # The disposable database carries the seeded seasons and no match at all.
    response = api_client.post("/v1/simulations", json={})

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["detail"] == "No season with matches"
    assert fake_queue.attempts == []


@pytest.mark.parametrize(
    "field",
    [
        pytest.param({"n_runs": 999}, id="n_runs-below-1000"),
        pytest.param({"n_runs": 50001}, id="n_runs-above-50000"),
        pytest.param({"seed": -1}, id="seed-negative"),
    ],
)
def test_create_simulation_rejects_an_out_of_range_body(
    api_client: TestClient,
    db_session: Session,
    fake_queue: FakeQueue,
    field: dict[str, int],
) -> None:
    season, _ = create_simulation_season(db_session)

    response = api_client.post("/v1/simulations", json={"season": season.code, **field})

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    problem = response.json()
    assert problem["detail"] == "Request validation failed"
    assert [error["loc"][-1] for error in problem["errors"]] == [next(iter(field))]
    assert fake_queue.attempts == []
    assert db_session.scalar(select(func.count()).select_from(SimulationRun)) == 0


def test_create_simulation_rejects_an_as_of_date_after_the_madrid_date(
    api_client: TestClient, db_session: Session, fake_queue: FakeQueue
) -> None:
    season, _ = create_simulation_season(db_session)
    tomorrow = madrid_today() + datetime.timedelta(days=1)

    response = api_client.post(
        "/v1/simulations",
        json={"season": season.code, "as_of_date": tomorrow.isoformat()},
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["detail"] == "as_of_date must not be later than the Madrid date"
    assert fake_queue.attempts == []
    assert db_session.scalar(select(func.count()).select_from(SimulationRun)) == 0


def test_get_simulation_returns_the_run_and_the_projected_table(
    api_client: TestClient, db_session: Session
) -> None:
    season, teams = create_simulation_season(db_session)
    run = create_simulation_run(
        db_session,
        season=season,
        status="succeeded",
        as_of_date=datetime.date(2098, 10, 1),
        n_runs=2500,
        seed=11,
        model_version="1.2.3",
        last_complete_matchday=12,
        n_fixed=120,
        n_simulated=260,
    )
    # Two clubs tie on the mean position and on the mean points, so the club
    # name decides between them and the identifier is never reached.
    create_team_result(
        db_session,
        run=run,
        team=teams[0],
        mean_position=2.5,
        mean_points=65.0,
        current_played=12,
        current_points=20,
        current_goal_difference=4,
        p_title=0.1,
        p_ucl=0.3,
        p_europe=0.5,
        p_relegation=0.05,
    )
    create_team_result(
        db_session,
        run=run,
        team=teams[1],
        mean_position=1.5,
        mean_points=70.0,
        points_min=40,
        points_pmf=[0.2, 0.3, 0.5],
        points_p05=50,
        points_p25=60,
        points_p50=70,
        points_p75=78,
        points_p95=85,
        position_probs=[0.5, 0.3, 0.2],
        p_title=0.5,
        p_ucl=0.8,
        p_europe=0.9,
        p_relegation=0.01,
    )
    create_team_result(db_session, run=run, team=teams[2], mean_position=2.5, mean_points=65.0)

    response = api_client.get(f"/v1/simulations/{run.id}")

    assert response.status_code == 200
    detail = response.json()
    assert set(detail) == {"run", "table"}
    assert_run_payload(detail["run"], run, season.code)
    assert detail["run"]["status"] == "succeeded"
    assert detail["run"]["model_version"] == "1.2.3"
    assert detail["run"]["n_fixed"] == 120
    assert detail["run"]["n_simulated"] == 260
    assert detail["run"]["last_complete_matchday"] == 12

    table = detail["table"]
    assert [row["team"]["name"] for row in table] == [teams[1].name, teams[0].name, teams[2].name]
    leader = table[0]
    assert set(leader) == TEAM_ROW_FIELDS
    assert leader["team"] == {"id": teams[1].id, "name": teams[1].name}
    assert leader["current"] == {"played": 0, "points": 0, "goal_difference": 0}
    assert leader["expected_points"] == 70.0
    assert leader["points_quantiles"] == {
        "p05": 50,
        "p25": 60,
        "p50": 70,
        "p75": 78,
        "p95": 85,
    }
    assert leader["points_distribution"] == {"min": 40, "probabilities": [0.2, 0.3, 0.5]}
    assert leader["expected_position"] == 1.5
    assert leader["position_probabilities"] == [0.5, 0.3, 0.2]
    assert leader["p_title"] == 0.5
    assert leader["p_ucl"] == 0.8
    assert leader["p_europe"] == 0.9
    assert leader["p_relegation"] == 0.01
    assert table[1]["current"] == {"played": 12, "points": 20, "goal_difference": 4}


def test_get_simulation_returns_an_empty_table_before_the_run_succeeded(
    api_client: TestClient, db_session: Session
) -> None:
    season, _ = create_simulation_season(db_session)
    run = create_simulation_run(db_session, season=season, status="running")

    response = api_client.get(f"/v1/simulations/{run.id}")

    assert response.status_code == 200
    detail = response.json()
    assert detail["run"]["status"] == "running"
    assert detail["table"] == []


def test_get_simulation_answers_404_for_an_unknown_run(api_client: TestClient) -> None:
    response = api_client.get(f"/v1/simulations/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["detail"] == "Simulation run not found"


def test_get_simulation_answers_422_for_a_non_uuid(api_client: TestClient) -> None:
    response = api_client.get("/v1/simulations/not-a-uuid")

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["detail"] == "Request validation failed"


def test_list_simulations_pages_one_ordered_list(
    api_client: TestClient, db_session: Session
) -> None:
    season, _ = create_simulation_season(db_session)
    oldest = create_simulation_run(
        db_session,
        season=season,
        requested_at=datetime.datetime(2098, 10, 1, 9, 0, tzinfo=datetime.UTC),
    )
    middle = create_simulation_run(
        db_session,
        season=season,
        status="succeeded",
        requested_at=datetime.datetime(2098, 10, 2, 9, 0, tzinfo=datetime.UTC),
    )
    newest = create_simulation_run(
        db_session,
        season=season,
        status="failed",
        as_of_date=datetime.date(2098, 10, 3),
        requested_at=datetime.datetime(2098, 10, 3, 9, 0, tzinfo=datetime.UTC),
        error="the model could not be fitted",
    )

    response = api_client.get("/v1/simulations", params={"season": season.code, "limit": 2})

    assert response.status_code == 200
    page = response.json()
    assert _ids(page["items"]) == [str(newest.id), str(middle.id)]
    assert page["total"] == 3
    assert page["limit"] == 2
    assert page["offset"] == 0
    assert_run_payload(page["items"][0], newest, season.code)

    second = api_client.get(
        "/v1/simulations", params={"season": season.code, "limit": 2, "offset": 2}
    )

    assert second.status_code == 200
    assert _ids(second.json()["items"]) == [str(oldest.id)]
    assert second.json()["total"] == 3
    assert second.json()["offset"] == 2


def test_list_simulations_applies_the_status_and_kind_filters(
    api_client: TestClient, db_session: Session
) -> None:
    season, _ = create_simulation_season(db_session)
    succeeded = create_simulation_run(db_session, season=season, status="succeeded")
    create_simulation_run(db_session, season=season, status="failed", error="the fit diverged")
    backfill = create_simulation_run(db_session, season=season, status="succeeded", kind="backfill")

    by_status = api_client.get(
        "/v1/simulations", params={"season": season.code, "status": "succeeded"}
    )
    by_kind = api_client.get("/v1/simulations", params={"season": season.code, "kind": "backfill"})
    by_both = api_client.get(
        "/v1/simulations",
        params={"season": season.code, "status": "succeeded", "kind": "live"},
    )

    assert by_status.status_code == 200
    assert sorted(_ids(by_status.json()["items"])) == sorted([str(succeeded.id), str(backfill.id)])
    assert by_status.json()["total"] == 2
    assert by_kind.status_code == 200
    assert _ids(by_kind.json()["items"]) == [str(backfill.id)]
    assert by_both.status_code == 200
    assert _ids(by_both.json()["items"]) == [str(succeeded.id)]


def test_list_simulations_defaults_to_the_season_with_the_latest_run(
    api_client: TestClient, db_session: Session
) -> None:
    # Neither season needs a club: this test is about which season the read
    # endpoints pick, and the default club names are unique in the table.
    older_season, _ = create_simulation_season(
        db_session, code="2097-98", start_year=2097, team_names=()
    )
    newer_season, _ = create_simulation_season(
        db_session, code="2098-99", start_year=2098, team_names=()
    )
    create_simulation_run(
        db_session,
        season=older_season,
        requested_at=datetime.datetime(2098, 12, 1, 9, 0, tzinfo=datetime.UTC),
    )
    newer = create_simulation_run(db_session, season=newer_season)

    response = api_client.get("/v1/simulations")

    assert response.status_code == 200
    page = response.json()
    assert _ids(page["items"]) == [str(newer.id)]
    assert page["total"] == 1


def test_list_simulations_answers_an_empty_page_when_no_season_has_a_run(
    api_client: TestClient,
) -> None:
    response = api_client.get("/v1/simulations")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_list_simulations_answers_404_for_an_unknown_season(api_client: TestClient) -> None:
    response = api_client.get("/v1/simulations", params={"season": "3000-01"})

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["detail"] == "Season not found"


def test_list_simulations_rejects_a_limit_above_200(api_client: TestClient) -> None:
    response = api_client.get("/v1/simulations", params={"limit": 201})

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert [error["loc"][-1] for error in response.json()["errors"]] == ["limit"]


def test_latest_simulation_answers_404_when_nothing_succeeded(
    api_client: TestClient, db_session: Session
) -> None:
    season, _ = create_simulation_season(db_session)
    create_simulation_run(db_session, season=season, status="queued")

    explicit = api_client.get("/v1/simulations/latest", params={"season": season.code})
    defaulted = api_client.get("/v1/simulations/latest")

    for response in (explicit, defaulted):
        assert response.status_code == 404
        assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
        assert response.json()["detail"] == "no succeeded simulation"


def test_latest_simulation_returns_the_newest_succeeded_run(
    api_client: TestClient, db_session: Session
) -> None:
    season, teams = create_simulation_season(db_session)
    older = create_simulation_run(
        db_session,
        season=season,
        status="succeeded",
        as_of_date=datetime.date(2098, 9, 1),
        requested_at=datetime.datetime(2098, 9, 1, 9, 0, tzinfo=datetime.UTC),
    )
    newer = create_simulation_run(
        db_session,
        season=season,
        status="succeeded",
        as_of_date=datetime.date(2098, 10, 1),
        requested_at=datetime.datetime(2098, 10, 1, 9, 0, tzinfo=datetime.UTC),
        model_version="2.0.0",
    )
    create_team_result(db_session, run=older, team=teams[0], mean_position=2.0, mean_points=50.0)
    create_team_result(db_session, run=newer, team=teams[0], mean_position=1.0, mean_points=75.0)

    response = api_client.get("/v1/simulations/latest", params={"season": season.code})

    assert response.status_code == 200
    detail = response.json()
    assert detail["run"]["id"] == str(newer.id)
    assert detail["run"]["model_version"] == "2.0.0"
    assert len(detail["table"]) == 1
    assert detail["table"][0]["expected_points"] == 75.0


def test_latest_simulation_defaults_to_the_season_with_the_latest_run(
    api_client: TestClient, db_session: Session
) -> None:
    older_season, _ = create_simulation_season(
        db_session, code="2097-98", start_year=2097, team_names=()
    )
    newer_season, newer_teams = create_simulation_season(
        db_session, code="2098-99", start_year=2098
    )
    # The older season finished last, so the answer proves the season is chosen
    # by its start year and not by the finish instant of its newest run.
    create_simulation_run(
        db_session,
        season=older_season,
        status="succeeded",
        requested_at=datetime.datetime(2098, 12, 1, 9, 0, tzinfo=datetime.UTC),
    )
    newer = create_simulation_run(
        db_session,
        season=newer_season,
        status="succeeded",
        requested_at=datetime.datetime(2098, 10, 1, 9, 0, tzinfo=datetime.UTC),
    )
    create_team_result(
        db_session, run=newer, team=newer_teams[0], mean_position=1.0, mean_points=70.0
    )

    response = api_client.get("/v1/simulations/latest")

    assert response.status_code == 200
    assert response.json()["run"]["season"] == newer_season.code


def test_history_returns_one_point_per_as_of_date_ascending(
    api_client: TestClient, db_session: Session
) -> None:
    season, teams = create_simulation_season(db_session)
    oldest = create_simulation_run(
        db_session,
        season=season,
        status="succeeded",
        as_of_date=datetime.date(2098, 9, 1),
        requested_at=datetime.datetime(2098, 9, 1, 9, 0, tzinfo=datetime.UTC),
        last_complete_matchday=2,
    )
    # The same as-of date was simulated twice; only the run that finished last
    # contributes the point.
    superseded = create_simulation_run(
        db_session,
        season=season,
        status="succeeded",
        as_of_date=datetime.date(2098, 10, 1),
        requested_at=datetime.datetime(2098, 10, 1, 8, 0, tzinfo=datetime.UTC),
        finished_at=datetime.datetime(2098, 10, 1, 12, 0, tzinfo=datetime.UTC),
        last_complete_matchday=5,
    )
    latest = create_simulation_run(
        db_session,
        season=season,
        status="succeeded",
        as_of_date=datetime.date(2098, 10, 1),
        requested_at=datetime.datetime(2098, 10, 1, 9, 0, tzinfo=datetime.UTC),
        finished_at=datetime.datetime(2098, 10, 1, 18, 0, tzinfo=datetime.UTC),
        last_complete_matchday=6,
    )
    # A date whose only run failed contributes no point at all.
    create_simulation_run(
        db_session,
        season=season,
        status="failed",
        as_of_date=datetime.date(2098, 11, 1),
        error="the fit diverged",
    )
    create_team_result(db_session, run=oldest, team=teams[0], mean_position=2.0, mean_points=60.0)
    create_team_result(db_session, run=oldest, team=teams[1], mean_position=1.0, mean_points=70.0)
    create_team_result(
        db_session, run=superseded, team=teams[0], mean_position=1.0, mean_points=99.0
    )
    create_team_result(
        db_session,
        run=latest,
        team=teams[0],
        mean_position=1.0,
        mean_points=80.0,
        p_title=0.6,
        p_ucl=0.9,
        p_europe=0.95,
        p_relegation=0.01,
    )
    create_team_result(db_session, run=latest, team=teams[1], mean_position=2.0, mean_points=70.0)

    response = api_client.get("/v1/simulations/history", params={"season": season.code})

    assert response.status_code == 200
    history = response.json()
    assert history["season"] == season.code
    assert [point["as_of_date"] for point in history["points"]] == ["2098-09-01", "2098-10-01"]
    assert [point["run_id"] for point in history["points"]] == [str(oldest.id), str(latest.id)]
    assert [point["kind"] for point in history["points"]] == ["live", "live"]
    assert [point["last_complete_matchday"] for point in history["points"]] == [2, 6]

    newest = history["points"][1]
    assert set(newest) == HISTORY_POINT_FIELDS
    assert [row["team"]["name"] for row in newest["teams"]] == [teams[0].name, teams[1].name]
    leader = newest["teams"][0]
    assert set(leader) == HISTORY_TEAM_FIELDS
    assert leader["team"] == {"id": teams[0].id, "name": teams[0].name}
    assert leader["expected_points"] == 80.0
    assert leader["expected_position"] == 1.0
    assert leader["p_title"] == 0.6
    assert leader["p_ucl"] == 0.9
    assert leader["p_europe"] == 0.95
    assert leader["p_relegation"] == 0.01


def test_history_orders_each_point_by_projected_position(
    api_client: TestClient, db_session: Session
) -> None:
    season, teams = create_simulation_season(db_session)
    run = create_simulation_run(db_session, season=season, status="succeeded")
    create_team_result(db_session, run=run, team=teams[0], mean_position=3.0, mean_points=40.0)
    create_team_result(db_session, run=run, team=teams[1], mean_position=1.0, mean_points=80.0)
    create_team_result(db_session, run=run, team=teams[2], mean_position=2.0, mean_points=80.0)

    response = api_client.get("/v1/simulations/history", params={"season": season.code})

    assert response.status_code == 200
    teams_of_point = response.json()["points"][0]["teams"]
    assert [row["team"]["name"] for row in teams_of_point] == [
        teams[1].name,
        teams[2].name,
        teams[0].name,
    ]


def test_history_defaults_to_the_season_with_the_latest_run(
    api_client: TestClient, db_session: Session
) -> None:
    # Neither season needs a club: the default club names are unique in the
    # table, and this test is about the season the collection picks.
    older_season, _ = create_simulation_season(
        db_session, code="2097-98", start_year=2097, team_names=()
    )
    newer_season, _ = create_simulation_season(
        db_session, code="2098-99", start_year=2098, team_names=()
    )
    create_simulation_run(
        db_session,
        season=older_season,
        status="succeeded",
        requested_at=datetime.datetime(2098, 12, 1, 9, 0, tzinfo=datetime.UTC),
    )
    newer = create_simulation_run(
        db_session,
        season=newer_season,
        status="succeeded",
        kind="backfill",
        requested_at=datetime.datetime(2098, 10, 1, 9, 0, tzinfo=datetime.UTC),
    )

    response = api_client.get("/v1/simulations/history")

    assert response.status_code == 200
    history = response.json()
    assert history["season"] == newer_season.code
    assert [point["run_id"] for point in history["points"]] == [str(newer.id)]
    assert history["points"][0]["kind"] == "backfill"


def test_history_filters_every_point_to_one_team(
    api_client: TestClient, db_session: Session
) -> None:
    season, teams = create_simulation_season(db_session)
    first = create_simulation_run(
        db_session,
        season=season,
        status="succeeded",
        as_of_date=datetime.date(2098, 9, 1),
        requested_at=datetime.datetime(2098, 9, 1, 9, 0, tzinfo=datetime.UTC),
    )
    second = create_simulation_run(
        db_session,
        season=season,
        status="succeeded",
        as_of_date=datetime.date(2098, 10, 1),
        requested_at=datetime.datetime(2098, 10, 1, 9, 0, tzinfo=datetime.UTC),
    )
    # A third date whose run does not mention the filtered club keeps its point
    # with an empty team list, so the series never silently loses a date.
    third = create_simulation_run(
        db_session,
        season=season,
        status="succeeded",
        as_of_date=datetime.date(2098, 10, 15),
        requested_at=datetime.datetime(2098, 10, 15, 9, 0, tzinfo=datetime.UTC),
    )
    for run in (first, second):
        create_team_result(db_session, run=run, team=teams[0], mean_position=1.0, mean_points=70.0)
        create_team_result(db_session, run=run, team=teams[1], mean_position=2.0, mean_points=60.0)
    create_team_result(db_session, run=third, team=teams[0], mean_position=1.0, mean_points=72.0)

    response = api_client.get(
        "/v1/simulations/history", params={"season": season.code, "team_id": teams[1].id}
    )

    assert response.status_code == 200
    points = response.json()["points"]
    assert [point["as_of_date"] for point in points] == ["2098-09-01", "2098-10-01", "2098-10-15"]
    assert [[row["team"]["id"] for row in point["teams"]] for point in points] == [
        [teams[1].id],
        [teams[1].id],
        [],
    ]


def test_history_answers_an_empty_history_for_a_stored_season_without_runs(
    api_client: TestClient, db_session: Session
) -> None:
    season, _ = create_simulation_season(db_session)

    response = api_client.get("/v1/simulations/history", params={"season": season.code})

    assert response.status_code == 200
    assert response.json() == {"season": season.code, "points": []}


def test_history_answers_404_for_an_unknown_season(api_client: TestClient) -> None:
    response = api_client.get("/v1/simulations/history", params={"season": "3000-01"})

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["detail"] == "Season not found"


def test_history_answers_404_for_an_unknown_team(
    api_client: TestClient, db_session: Session
) -> None:
    season, _ = create_simulation_season(db_session)

    response = api_client.get(
        "/v1/simulations/history", params={"season": season.code, "team_id": ABSENT_TEAM_ID}
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["detail"] == "Team not found"


def test_history_answers_404_when_no_season_has_a_run(api_client: TestClient) -> None:
    response = api_client.get("/v1/simulations/history")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["detail"] == "no simulation"


def test_a_created_run_is_readable_at_its_location(
    api_client: TestClient, db_session: Session, fake_queue: FakeQueue
) -> None:
    season, _ = create_simulation_season(db_session)
    created = api_client.post("/v1/simulations", json={"season": season.code})
    assert created.status_code == 202
    run_id = uuid.UUID(created.json()["id"])

    detail = api_client.get(created.headers["location"])
    listed = api_client.get("/v1/simulations", params={"season": season.code})

    assert detail.status_code == 200
    assert detail.json()["run"]["id"] == str(run_id)
    assert detail.json()["run"]["status"] == "queued"
    assert detail.json()["table"] == []
    assert listed.status_code == 200
    assert _ids(listed.json()["items"]) == [str(run_id)]
    stored = db_session.get(SimulationRun, run_id)
    assert stored is not None
    assert stored.status == "queued"
    assert fake_queue.attempts == [run_id]
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(SimulationTeamResult)
            .where(SimulationTeamResult.run_id == run_id)
        )
        == 0
    )
