"""Integration tests for the season-simulation service.

Every row in this module is synthetic: the club names are invented, the
scorelines are hand-built and the season code does not exist in the real
loader. The fixture is self-contained for the same reason as
``test_live_guards_integration.py``: this package has no shared ``conftest.py``,
and the suite drops the schema it connects to.

The tests observe the public service behaviour: what a request returns, what a
claim does to an abandoned row, which match data the engine is allowed to see,
and what the successful or failed run stores.
"""

import datetime
import os
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import alembic.command
import pytest
from lmi_core.config import Settings
from lmi_core.db import models as db_models
from lmi_core.db.alembic_support import alembic_config
from lmi_core.db.models import Match, Season, SimulationRun, SimulationTeamResult, Team
from lmi_core.db.session import make_engine, session_factory
from lmi_core.db.testing import require_disposable_database
from lmi_core.simulation import service as simulation_service
from lmi_core.simulation.engine import Qualification, simulate_season
from lmi_core.standings import MatchResult, compute_standings
from sqlalchemy import Select, false, func, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

POSTGRES_ENVIRONMENT_VARIABLES = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)
"""The variables the fixture requires; a missing one is a hard failure."""

SEASON_CODE = "2099-00"
"""A season code that cannot collide with a real loader season."""

START_YEAR = 2099
"""The start year of the synthetic season."""

TEAM_NAMES = (
    "Northbridge Rovers",
    "Southfield Athletic",
    "Eastgate Town",
    "Westvale City",
)
"""Four invented clubs, enough for a double round robin."""

QUALIFICATION = Qualification(ucl_positions=1, europe_positions=2, relegation_positions=1)
"""Thresholds that fit a four-team league."""

N_RUNS = 100
"""The smallest run count the schema accepts, so the suite stays fast."""

_PAIRINGS = (
    ((0, 1), (2, 3)),
    ((0, 2), (3, 1)),
    ((0, 3), (1, 2)),
    ((1, 0), (3, 2)),
    ((2, 0), (1, 3)),
    ((3, 0), (2, 1)),
)
"""The six matchdays of a four-team double round robin, as ordered pairs."""

_TEAM_RESULT_COLUMNS = (
    "team_id",
    "current_played",
    "current_points",
    "current_goal_difference",
    "mean_points",
    "points_p05",
    "points_p25",
    "points_p50",
    "points_p75",
    "points_p95",
    "points_min",
    "points_pmf",
    "mean_position",
    "position_probs",
    "p_title",
    "p_ucl",
    "p_europe",
    "p_relegation",
)
"""The stored columns a reproducibility comparison has to cover."""

_RESULT_COLUMNS = ", ".join(_TEAM_RESULT_COLUMNS)
"""The comparison column list as one SQL fragment."""


@dataclass(frozen=True)
class Database:
    """The throwaway database and a session factory bound to it."""

    engine: Engine
    sessions: sessionmaker[Session]


@dataclass(frozen=True)
class MatchSpec:
    """One synthetic match to store."""

    home_name: str
    away_name: str
    kickoff_date: datetime.date
    status: str = "scheduled"
    home_goals: int | None = None
    away_goals: int | None = None
    matchday: int | None = None


@pytest.fixture
def database() -> Iterator[Database]:
    """Yield a database migrated to ``head`` with an empty ``public`` schema.

    Every test starts from ``DROP SCHEMA public CASCADE`` plus ``CREATE SCHEMA
    public`` and then upgrades to ``head``, so the tests do not depend on each
    other or on their order.

    Raises:
        pytest.fail: the destructive-test guard refused the environment, or one of
            the ``POSTGRES_*`` variables is missing. The message names the
            variable to set and never a value.
    """
    try:
        require_disposable_database()
    except RuntimeError as refusal:
        pytest.fail(str(refusal), pytrace=False)

    missing = [name for name in POSTGRES_ENVIRONMENT_VARIABLES if not os.environ.get(name)]
    if missing:
        pytest.fail(
            "the integration tests need a live PostgreSQL: "
            + ", ".join(missing)
            + " is not set; run `make test-integration`",
            pytrace=False,
        )

    settings = Settings()
    engine = make_engine(settings)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        alembic.command.upgrade(alembic_config(settings), "head")
        yield Database(engine=engine, sessions=session_factory(engine))
    finally:
        engine.dispose()


def _create_season(sessions: sessionmaker[Session]) -> int:
    """Store the synthetic season and return its id."""
    with sessions.begin() as session:
        season = Season(code=SEASON_CODE, start_year=START_YEAR, fd_code="2099-2100")
        session.add(season)
        session.flush()
        return season.id


def _create_teams(
    sessions: sessionmaker[Session], names: Sequence[str] = TEAM_NAMES
) -> dict[str, int]:
    """Store the synthetic teams and return their ids by name."""
    with sessions.begin() as session:
        teams = [Team(name=name) for name in names]
        session.add_all(teams)
        session.flush()
        return {team.name: team.id for team in teams}


def _result(home_goals: int, away_goals: int) -> str:
    """Return the stored result letter of a scoreline."""
    if home_goals > away_goals:
        return "H"
    if home_goals == away_goals:
        return "D"
    return "A"


def _store_matches(
    sessions: sessionmaker[Session],
    *,
    season_id: int,
    teams: dict[str, int],
    specs: Sequence[MatchSpec],
) -> None:
    """Store every synthetic match in one transaction."""
    with sessions.begin() as session:
        for spec in specs:
            result = None
            if spec.status == "played":
                if spec.home_goals is None or spec.away_goals is None:
                    raise ValueError("a played match needs both goals")
                result = _result(spec.home_goals, spec.away_goals)
            session.add(
                Match(
                    season_id=season_id,
                    kickoff_date=spec.kickoff_date,
                    home_team_id=teams[spec.home_name],
                    away_team_id=teams[spec.away_name],
                    matchday=spec.matchday,
                    status=spec.status,
                    home_goals=spec.home_goals,
                    away_goals=spec.away_goals,
                    result=result,
                    withdrawn_at=(
                        datetime.datetime.now(datetime.UTC) if spec.status == "withdrawn" else None
                    ),
                )
            )


def _season_specs(
    start: datetime.date, *, played_through: int, with_matchdays: bool = True
) -> list[MatchSpec]:
    """Return the synthetic double round robin with its first matchdays played."""
    specs: list[MatchSpec] = []
    for matchday, (first_pair, second_pair) in enumerate(_PAIRINGS, start=1):
        for slot, (home_index, away_index) in enumerate((first_pair, second_pair)):
            kickoff = start + datetime.timedelta(days=7 * (matchday - 1) + slot)
            played = matchday <= played_through
            specs.append(
                MatchSpec(
                    home_name=TEAM_NAMES[home_index],
                    away_name=TEAM_NAMES[away_index],
                    kickoff_date=kickoff,
                    status="played" if played else "scheduled",
                    home_goals=(matchday + slot) % 4 if played else None,
                    away_goals=(matchday + 2 * slot) % 3 if played else None,
                    matchday=matchday if with_matchdays else None,
                )
            )
    return specs


def _request_run(
    database: Database, as_of_date: datetime.date, *, seed: int = 7
) -> tuple[uuid.UUID, bool]:
    """Request the synthetic season and return the run id and the enqueue flag."""
    with database.sessions() as session:
        run, enqueue = simulation_service.create_run(
            session,
            season_code=SEASON_CODE,
            as_of_date=as_of_date,
            n_runs=N_RUNS,
            seed=seed,
            trigger="cli",
            qualification=QUALIFICATION,
        )
        return run.id, enqueue


def _load_run(database: Database, run_id: uuid.UUID) -> SimulationRun:
    """Return one stored run."""
    with database.sessions() as session:
        run = session.get(SimulationRun, run_id)
        if run is None:
            raise AssertionError(f"simulation run {run_id} is missing")
        return run


def _team_rows(database: Database, run_id: uuid.UUID) -> list[SimulationTeamResult]:
    """Return one run's team rows in team-id order."""
    with database.sessions() as session:
        return list(
            session.scalars(
                select(SimulationTeamResult)
                .where(SimulationTeamResult.run_id == run_id)
                .order_by(SimulationTeamResult.team_id)
            )
        )


def _fixture_rows(database: Database, run_id: uuid.UUID) -> list[db_models.SimulationFixture]:
    """Return one run's stored fixture probabilities in match-id order."""
    with database.sessions() as session:
        return list(
            session.scalars(
                select(db_models.SimulationFixture)
                .where(db_models.SimulationFixture.run_id == run_id)
                .order_by(db_models.SimulationFixture.match_id)
            )
        )


def _next_match_rows(database: Database, run_id: uuid.UUID) -> list[db_models.SimulationNextMatch]:
    """Return one run's stored next-match counts in team and outcome order."""
    with database.sessions() as session:
        return list(
            session.scalars(
                select(db_models.SimulationNextMatch)
                .where(db_models.SimulationNextMatch.run_id == run_id)
                .order_by(
                    db_models.SimulationNextMatch.team_id,
                    db_models.SimulationNextMatch.outcome,
                )
            )
        )


def _match_ids_by_fixture(
    database: Database, season_id: int
) -> dict[tuple[datetime.date, int, int], int]:
    """Return each stored match id by its kickoff date and two team ids."""
    with database.sessions() as session:
        matches = list(session.scalars(select(Match).where(Match.season_id == season_id)))
    return {
        (match.kickoff_date, match.home_team_id, match.away_team_id): match.id for match in matches
    }


def _is_simulated(spec: MatchSpec, as_of_date: datetime.date) -> bool:
    """Return whether the service simulates one synthetic match."""
    return spec.status != "withdrawn" and not (
        spec.status == "played" and spec.kickoff_date < as_of_date
    )


def _simulated_fixture_ids(
    specs: Sequence[MatchSpec],
    match_ids: dict[tuple[datetime.date, int, int], int],
    teams: dict[str, int],
    as_of_date: datetime.date,
) -> set[int]:
    """Return the ids of the simulated fixtures, computed from the specs alone."""
    return {
        match_ids[(spec.kickoff_date, teams[spec.home_name], teams[spec.away_name])]
        for spec in specs
        if _is_simulated(spec, as_of_date)
    }


def _next_match_ids(
    specs: Sequence[MatchSpec],
    match_ids: dict[tuple[datetime.date, int, int], int],
    teams: dict[str, int],
    as_of_date: datetime.date,
) -> dict[int, int]:
    """Return each team's next match id from the specs.

    That is its first simulated fixture in kickoff order that is dated on or after
    the as-of date: an earlier, still scheduled (postponed) fixture is simulated
    but never named.
    """
    first: dict[int, int] = {}
    for spec in sorted(specs, key=lambda item: item.kickoff_date):
        if not _is_simulated(spec, as_of_date) or spec.kickoff_date < as_of_date:
            continue
        home_id = teams[spec.home_name]
        away_id = teams[spec.away_name]
        match_id = match_ids[(spec.kickoff_date, home_id, away_id)]
        first.setdefault(home_id, match_id)
        first.setdefault(away_id, match_id)
    return first


def _snapshot(
    database: Database, run_id: uuid.UUID
) -> list[tuple[int, float, list[float], float, float, float, float]]:
    """Return the L12 comparison columns row by row."""
    return [
        (
            row.team_id,
            row.mean_points,
            list(row.position_probs),
            row.p_title,
            row.p_ucl,
            row.p_europe,
            row.p_relegation,
        )
        for row in _team_rows(database, run_id)
    ]


def _fixed_results(
    teams: dict[str, int], specs: Sequence[MatchSpec], as_of_date: datetime.date
) -> list[MatchResult]:
    """Return the results the service must count as fixed, from the same rules."""
    results: list[MatchResult] = []
    for spec in specs:
        if spec.status != "played" or spec.kickoff_date >= as_of_date:
            continue
        if spec.home_goals is None or spec.away_goals is None:
            raise AssertionError("a played fixture needs both goals")
        results.append(
            MatchResult(
                home_team_id=teams[spec.home_name],
                away_team_id=teams[spec.away_name],
                home_goals=spec.home_goals,
                away_goals=spec.away_goals,
                kickoff_date=spec.kickoff_date,
            )
        )
    return results


def _instrument_simulation(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count calls to the module's ``simulate_season`` through a wrapper."""
    calls: list[int] = []
    original = simulate_season

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(simulation_service, "simulate_season", counting)
    return calls


def _set_status_running(database: Database, run_id: uuid.UUID, *, minutes_ago: int) -> None:
    """Move a queued run to running with a database-clock start in the past."""
    with database.sessions.begin() as session:
        session.execute(
            text(
                """
                UPDATE simulation_runs
                   SET status = 'running',
                       started_at = now() - (:minutes * interval '1 minute')
                 WHERE id = :run_id
                """
            ),
            {"minutes": minutes_ago, "run_id": run_id},
        )


def _backdate_request(database: Database, run_id: uuid.UUID, *, minutes: int) -> None:
    """Move a queued run's requested_at into the past by the database clock."""
    with database.sessions.begin() as session:
        session.execute(
            text(
                """
                UPDATE simulation_runs
                   SET requested_at = now() - (:minutes * interval '1 minute')
                 WHERE id = :run_id
                """
            ),
            {"minutes": minutes, "run_id": run_id},
        )


def _exclusive_team_rows(
    database: Database, first: uuid.UUID, second: uuid.UUID
) -> list[tuple[object, ...]]:
    """Return the rows of ``first`` that ``second`` does not have."""
    statement = (
        f"(SELECT {_RESULT_COLUMNS} FROM simulation_team_results WHERE run_id = :first) "
        f"EXCEPT (SELECT {_RESULT_COLUMNS} FROM simulation_team_results WHERE run_id = :second)"
    )
    with database.engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(text(statement), {"first": first, "second": second})
        ]


def _poison_future_results(database: Database, season_id: int, as_of_date: datetime.date) -> None:
    """Mark every scheduled future match as a 9-0 home win."""
    with database.sessions.begin() as session:
        session.execute(
            text(
                """
                UPDATE matches
                   SET status = 'played',
                       home_goals = 9,
                       away_goals = 0,
                       result = 'H'
                 WHERE season_id = :season_id
                   AND kickoff_date >= :as_of_date
                   AND status <> 'withdrawn'
                """
            ),
            {"season_id": season_id, "as_of_date": as_of_date},
        )


def test_l12_played_season_has_no_fixtures_and_one_hot_positions(
    database: Database,
) -> None:
    """L12: a fully played season simulates nothing and reproduces the table."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=6)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)
    as_of = start + datetime.timedelta(days=60)

    run_id, enqueue = _request_run(database, as_of)
    assert enqueue is True
    simulation_service.execute_run(database.sessions, run_id)

    run = _load_run(database, run_id)
    assert run.status == "succeeded"
    assert run.n_simulated == 0
    assert run.n_fixed == len(specs)

    fixed = _fixed_results(teams, specs, as_of)
    team_names = {team_id: name for name, team_id in teams.items()}
    standings = {row.team_id: row for row in compute_standings(team_names, fixed)}
    rows = _team_rows(database, run_id)
    assert len(rows) == len(teams)
    for row in rows:
        standing = standings[row.team_id]
        assert row.current_played == standing.played
        assert row.current_points == standing.points
        assert row.current_goal_difference == standing.goal_difference
        assert row.mean_points == pytest.approx(standing.points)
        probabilities = list(row.position_probs)
        assert len(probabilities) == len(teams)
        assert probabilities[standing.position - 1] == pytest.approx(1.0)
        assert sum(probabilities) == pytest.approx(1.0)


def test_l12_poisoned_future_results_do_not_change_the_simulation(
    database: Database,
) -> None:
    """L12: a result dated on or after as-of is re-simulated, never counted."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=3)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)
    as_of = start + datetime.timedelta(days=19)

    first_id, first_enqueue = _request_run(database, as_of)
    assert first_enqueue is True
    simulation_service.execute_run(database.sessions, first_id)
    first = _snapshot(database, first_id)
    assert _load_run(database, first_id).n_simulated == len(specs) - 6

    _poison_future_results(database, season_id, as_of)
    with database.sessions() as session:
        stored_goals = session.execute(
            text(
                "SELECT home_goals, away_goals FROM matches "
                "WHERE season_id = :season_id AND kickoff_date >= :as_of_date "
                "ORDER BY id LIMIT 1"
            ),
            {"season_id": season_id, "as_of_date": as_of},
        ).one()
    assert (stored_goals.home_goals, stored_goals.away_goals) == (9, 0)

    second_id, second_enqueue = _request_run(database, as_of)
    assert second_id != first_id
    assert second_enqueue is True
    simulation_service.execute_run(database.sessions, second_id)

    assert _snapshot(database, second_id) == first
    assert _load_run(database, second_id).n_simulated == len(specs) - 6


def test_l16_two_calls_execute_once(database: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """L16: the claim makes a second execute_run on the same id a no-op."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=3)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)
    as_of = start + datetime.timedelta(days=19)
    calls = _instrument_simulation(monkeypatch)

    run_id, _ = _request_run(database, as_of)
    simulation_service.execute_run(database.sessions, run_id)
    simulation_service.execute_run(database.sessions, run_id)

    assert calls == [1]
    assert _load_run(database, run_id).status == "succeeded"
    assert len(_team_rows(database, run_id)) == len(teams)


def test_l16_abandoned_running_row_is_reclaimed(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L16: a running row started 31 minutes ago is claimed again."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=3)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)
    as_of = start + datetime.timedelta(days=19)
    calls = _instrument_simulation(monkeypatch)

    run_id, _ = _request_run(database, as_of)
    _set_status_running(database, run_id, minutes_ago=31)
    simulation_service.execute_run(database.sessions, run_id)

    assert calls == [1]
    assert _load_run(database, run_id).status == "succeeded"
    assert len(_team_rows(database, run_id)) == len(teams)


def test_l16_recent_running_row_is_left_untouched(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L16: a running row started 5 minutes ago belongs to another executor."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=3)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)
    as_of = start + datetime.timedelta(days=19)
    calls = _instrument_simulation(monkeypatch)

    run_id, _ = _request_run(database, as_of)
    _set_status_running(database, run_id, minutes_ago=5)
    started_before = _load_run(database, run_id).started_at

    simulation_service.execute_run(database.sessions, run_id)

    run = _load_run(database, run_id)
    assert calls == []
    assert run.status == "running"
    assert run.started_at == started_before
    assert _team_rows(database, run_id) == []


def test_reproducible_runs_store_identical_team_results(database: Database) -> None:
    """Two runs with the same key and seed store the same rows."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=3)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)
    as_of = start + datetime.timedelta(days=19)

    first_id, _ = _request_run(database, as_of)
    simulation_service.execute_run(database.sessions, first_id)
    second_id, _ = _request_run(database, as_of)
    simulation_service.execute_run(database.sessions, second_id)

    assert first_id != second_id
    assert _exclusive_team_rows(database, first_id, second_id) == []
    assert _exclusive_team_rows(database, second_id, first_id) == []


def test_create_run_reuses_active_rows_and_inserts_after_success(
    database: Database,
) -> None:
    """create_run returns the active row, reports staleness and inserts after success."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=6)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)
    as_of = start + datetime.timedelta(days=60)

    run_id, enqueue = _request_run(database, as_of)
    assert enqueue is True
    assert _load_run(database, run_id).status == "queued"

    same_id, same_enqueue = _request_run(database, as_of)
    assert same_id == run_id
    assert same_enqueue is False

    _backdate_request(database, run_id, minutes=2)
    stale_id, stale_enqueue = _request_run(database, as_of)
    assert stale_id == run_id
    assert stale_enqueue is True

    simulation_service.execute_run(database.sessions, run_id)
    assert _load_run(database, run_id).status == "succeeded"

    new_id, new_enqueue = _request_run(database, as_of)
    assert new_id != run_id
    assert new_enqueue is True

    with database.sessions() as session:
        count = session.scalar(select(func.count()).select_from(SimulationRun))
    assert count == 2


def test_a_conflict_whose_winner_finished_inserts_a_new_run(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conflict whose winner became terminal still serves the request.

    A concurrent caller can take the natural key between the read that finds
    nothing and the insert, and can then finish before the read that follows the
    rejected insert. Nothing holds the key at that point, so the request has to
    be served instead of failing. The race is staged through the statement seam:
    the first read reports a free key while the winner row already exists, and
    the read after the conflict finishes the winner before it answers.
    """
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=6)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)
    as_of = start + datetime.timedelta(days=60)
    winner_id, _ = _request_run(database, as_of)

    reads: list[int] = []
    real_statement = simulation_service._active_run_statement

    def racing_statement(
        season: int, as_of_date: datetime.date, n_runs: int, seed: int
    ) -> Select[SimulationRun]:
        reads.append(1)
        if len(reads) == 1:
            return select(SimulationRun).where(false())
        with database.sessions.begin() as session:
            session.execute(
                update(SimulationRun)
                .where(SimulationRun.id == winner_id)
                .values(status="succeeded", started_at=func.now(), finished_at=func.now())
            )
        return real_statement(season, as_of_date, n_runs, seed)

    monkeypatch.setattr(simulation_service, "_active_run_statement", racing_statement)

    run_id, enqueue = _request_run(database, as_of)

    assert enqueue is True
    assert run_id != winner_id
    assert _load_run(database, winner_id).status == "succeeded"
    assert _load_run(database, run_id).status == "queued"
    with database.sessions() as session:
        count = session.scalar(select(func.count()).select_from(SimulationRun))
    assert count == 2


def test_backfill_dates_with_matchdays_uses_played_matchdays(database: Database) -> None:
    """A fully played matchday contributes the day after its last kickoff."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=1)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)

    with database.sessions() as session:
        dates = simulation_service.backfill_dates(session, SEASON_CODE)

    assert dates == [
        start - datetime.timedelta(days=1),
        start + datetime.timedelta(days=2),
    ]


def test_backfill_dates_without_matchdays_uses_weekly_fallback(database: Database) -> None:
    """Without a matchday, dates advance in weeks and stop at the last played kickoff."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=3, with_matchdays=False)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)

    with database.sessions() as session:
        dates = simulation_service.backfill_dates(session, SEASON_CODE)

    assert dates == [
        start - datetime.timedelta(days=1),
        start + datetime.timedelta(days=7),
        start + datetime.timedelta(days=14),
    ]


def test_last_complete_matchday_stops_at_the_first_incomplete(database: Database) -> None:
    """A complete matchday followed by an incomplete one stores the complete one."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=1)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)
    as_of = start + datetime.timedelta(days=30)

    run_id, _ = _request_run(database, as_of)
    simulation_service.execute_run(database.sessions, run_id)

    assert _load_run(database, run_id).last_complete_matchday == 1


def test_last_complete_matchday_is_null_without_matchdays(database: Database) -> None:
    """A season without a matchday stores NULL."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=3, with_matchdays=False)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)
    as_of = start + datetime.timedelta(days=30)

    run_id, _ = _request_run(database, as_of)
    simulation_service.execute_run(database.sessions, run_id)

    assert _load_run(database, run_id).last_complete_matchday is None


def test_failure_is_stored_without_reraising(database: Database) -> None:
    """L16: a run whose season data raises is stored failed and returns quietly."""
    _create_season(database.sessions)
    run_id, enqueue = _request_run(database, datetime.date(2099, 1, 1))
    assert enqueue is True

    simulation_service.execute_run(database.sessions, run_id)

    run = _load_run(database, run_id)
    assert run.status == "failed"
    assert run.error is not None
    assert "teams must not be empty" in run.error
    assert len(run.error) <= 2000
    assert _team_rows(database, run_id) == []


def test_executed_run_stores_one_row_per_simulated_fixture(database: Database) -> None:
    """Every simulated fixture stores one probability triple that sums to one."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=3)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)
    as_of = start + datetime.timedelta(days=19)

    run_id, _ = _request_run(database, as_of)
    simulation_service.execute_run(database.sessions, run_id)

    run = _load_run(database, run_id)
    match_ids = _match_ids_by_fixture(database, season_id)
    expected_ids = _simulated_fixture_ids(specs, match_ids, teams, as_of)
    rows = _fixture_rows(database, run_id)
    assert {row.match_id for row in rows} == expected_ids
    assert len(rows) == run.n_simulated
    for row in rows:
        assert abs(row.p_home + row.p_draw + row.p_away - 1.0) < 1e-6


def test_next_match_rows_name_each_teams_first_simulated_fixture(
    database: Database,
) -> None:
    """Every team stores three rows for one fixture: its first simulated one."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=3)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)
    as_of = start + datetime.timedelta(days=19)

    run_id, _ = _request_run(database, as_of)
    simulation_service.execute_run(database.sessions, run_id)

    match_ids = _match_ids_by_fixture(database, season_id)
    expected = _next_match_ids(specs, match_ids, teams, as_of)
    rows = _next_match_rows(database, run_id)
    assert len(rows) == 3 * len(teams)
    assert set(expected) == set(teams.values())
    stored_ids_by_team: dict[int, set[int]] = {}
    outcomes_by_team: dict[int, set[str]] = {}
    for row in rows:
        stored_ids_by_team.setdefault(row.team_id, set()).add(row.match_id)
        outcomes_by_team.setdefault(row.team_id, set()).add(row.outcome)
    assert set(stored_ids_by_team) == set(teams.values())
    for team_id, stored_ids in stored_ids_by_team.items():
        assert stored_ids == {expected[team_id]}
        assert outcomes_by_team[team_id] == {"win", "draw", "loss"}


def test_next_match_counts_reproduce_the_stored_probabilities(database: Database) -> None:
    """The per-outcome counts mix back to the stored qualification probabilities."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=3)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)
    as_of = start + datetime.timedelta(days=19)

    run_id, _ = _request_run(database, as_of)
    simulation_service.execute_run(database.sessions, run_id)

    run = _load_run(database, run_id)
    n_runs = run.n_runs
    team_rows = {row.team_id: row for row in _team_rows(database, run_id)}
    grouped: dict[int, list[db_models.SimulationNextMatch]] = {}
    for row in _next_match_rows(database, run_id):
        grouped.setdefault(row.team_id, []).append(row)
    assert set(grouped) == set(team_rows)
    for team_id, rows in grouped.items():
        team_row = team_rows[team_id]
        assert sum(row.n_outcome for row in rows) == n_runs
        relegation = sum(
            (row.n_outcome / n_runs) * (row.n_relegated / row.n_outcome)
            for row in rows
            if row.n_outcome
        )
        europe = sum(
            (row.n_outcome / n_runs) * (row.n_europe / row.n_outcome)
            for row in rows
            if row.n_outcome
        )
        title = sum(
            (row.n_outcome / n_runs) * (row.n_title / row.n_outcome)
            for row in rows
            if row.n_outcome
        )
        assert abs(relegation - team_row.p_relegation) < 1e-9
        assert abs(europe - team_row.p_europe) < 1e-9
        assert abs(title - team_row.p_title) < 1e-9


def test_detail_rows_stay_within_the_storage_bound(database: Database) -> None:
    """The two detail tables hold at most 440 rows for one run."""
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=3)
    _store_matches(database.sessions, season_id=season_id, teams=teams, specs=specs)
    as_of = start + datetime.timedelta(days=19)

    run_id, _ = _request_run(database, as_of)
    simulation_service.execute_run(database.sessions, run_id)

    run = _load_run(database, run_id)
    fixtures = _fixture_rows(database, run_id)
    next_matches = _next_match_rows(database, run_id)
    assert len(fixtures) == run.n_simulated
    assert len(next_matches) == 3 * len(teams)
    assert len(fixtures) + len(next_matches) <= 440


@pytest.mark.parametrize("as_of_days", [24, 28], ids=["between-matchdays", "on-a-kickoff-date"])
def test_a_postponed_fixture_is_simulated_but_never_named_the_next_match(
    database: Database, as_of_days: int
) -> None:
    """A scheduled fixture dated before the as-of date is not a club's next match.

    The season rows are stored with the last matchday first, so the match id runs
    opposite to the kickoff order. The fourth matchday is dated before the as-of
    date and still scheduled: the service simulates it (it has not been played)
    but names the first fixture dated on or after the as-of date instead, which is
    what the focus page lists first as upcoming. Naming the earliest kickoff would
    pick the postponed one, and ordering by ``sim_date`` would break the tie of
    the collapsed dates by match id.
    """
    season_id = _create_season(database.sessions)
    teams = _create_teams(database.sessions)
    start = datetime.date(2099, 1, 1)
    specs = _season_specs(start, played_through=3)
    _store_matches(
        database.sessions,
        season_id=season_id,
        teams=teams,
        specs=list(reversed(specs)),
    )
    as_of = start + datetime.timedelta(days=as_of_days)

    run_id, _ = _request_run(database, as_of)
    simulation_service.execute_run(database.sessions, run_id)

    match_ids = _match_ids_by_fixture(database, season_id)
    expected = _next_match_ids(specs, match_ids, teams, as_of)
    assert set(expected) == set(teams.values())
    stored_ids_by_team: dict[int, set[int]] = {}
    for row in _next_match_rows(database, run_id):
        stored_ids_by_team.setdefault(row.team_id, set()).add(row.match_id)
    assert {team_id: min(ids) for team_id, ids in stored_ids_by_team.items()} == expected
    assert all(len(ids) == 1 for ids in stored_ids_by_team.values())

    # A fixture dated exactly on the as-of date is eligible (the rule is >=), which
    # the second case checks: its clubs name that fixture, not a later one.
    # The guard has teeth only while a postponed fixture exists and is simulated:
    # the fourth matchday is dated before the as-of date and every club plays it.
    postponed = {
        match_id
        for (kickoff, _home, _away), match_id in match_ids.items()
        if kickoff < as_of and kickoff >= start + datetime.timedelta(days=21)
    }
    assert postponed
    assert postponed <= _simulated_fixture_ids(specs, match_ids, teams, as_of)
    assert postponed.isdisjoint(
        {match_id for ids in stored_ids_by_team.values() for match_id in ids}
    )
