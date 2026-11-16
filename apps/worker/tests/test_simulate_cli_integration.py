"""End-to-end integration tests for the ``simulate`` worker command.

Every test here runs :func:`lmi_worker.cli.main` against a migrated, seeded and
disposable PostgreSQL, so this module exercises the real path end to end: the
command line, the season split, the Dixon-Coles fit, the engine, the stored run,
the stored team rows and the rendered table. Nothing is replaced by a stand, and
no argument is faked; the only synthetic part is the season itself.

The season is invented and cannot collide with real data: code ``2030-31`` is a
seeded season row with no matches, and the eight clubs come from the seeded team
table. Every match, matchday, scoreline and date is written by this module, so a
test never depends on ingestion or on the network.

The three scenarios mirror the acceptance checks of the task: a live run prints
its projected table and stores one succeeded run, the same arguments twice store
byte-identical team rows, and a second ``--backfill`` adds no run.
"""

import datetime
from typing import TYPE_CHECKING

import pytest
from lmi_core.db.models import Match, Season, SimulationRun, SimulationTeamResult, Team
from lmi_worker.cli import main
from sqlalchemy import insert, select, text

if TYPE_CHECKING:
    from conftest import MigratedDatabase

pytestmark = pytest.mark.integration

SEASON_CODE = "2030-31"
"""A seeded season with no real match; the last one the seed covers."""

FIRST_MATCHDAY = datetime.date(2030, 8, 15)
"""The kickoff date of matchday one; every later matchday is one week after it."""

N_MATCHDAYS = 14
"""A double round robin over eight clubs."""

N_TEAMS = 8
"""Clubs in the invented league; the default qualification needs more than six."""

N_RUNS = 100
"""The smallest run count the schema accepts, which keeps the suite quick."""

SEED = 0
"""The documented default seed."""

FIXED_AT_MID_SEASON = 28
"""Matches before matchday eight: seven played matchdays of four matches."""

SIMULATED_AT_MID_SEASON = 28
"""Matches from matchday eight on, all re-simulated even when stored as played."""

DEFINITIVE_ROW = (
    "select count(*) from (("
    "select team_id, mean_points, p_title, p_ucl, p_europe, p_relegation, position_probs "
    "from simulation_team_results where run_id = :a) except ("
    "select team_id, mean_points, p_title, p_ucl, p_europe, p_relegation, position_probs "
    "from simulation_team_results where run_id = :b)) differing"
)
"""The SQL ``EXCEPT`` that must stay empty for two runs of the same arguments."""


def _matchday_date(matchday: int) -> datetime.date:
    """Return the kickoff date of one matchday."""
    return FIRST_MATCHDAY + datetime.timedelta(days=7 * (matchday - 1))


def _season_id(db: MigratedDatabase) -> int:
    """Return the id of the invented season."""
    with db.factories() as session:
        season_id = session.scalar(select(Season.id).where(Season.code == SEASON_CODE))
    assert season_id is not None
    return int(season_id)


def _team_ids(db: MigratedDatabase) -> list[int]:
    """Return the ids of the clubs that play the invented season, by name."""
    with db.factories() as session:
        ids = list(session.scalars(select(Team.id).order_by(Team.name).limit(N_TEAMS)))
    assert len(ids) == N_TEAMS
    return [int(team_id) for team_id in ids]


def _scoreline(matchday: int) -> tuple[int, int]:
    """Return a deterministic, non-degenerate scoreline for one matchday."""
    return 1 + matchday % 3, matchday % 2


def _result(home_goals: int, away_goals: int) -> str:
    """Return the stored result letter of a scoreline."""
    if home_goals > away_goals:
        return "H"
    return "D" if home_goals == away_goals else "A"


def _fixtures(team_ids: list[int]) -> list[tuple[int, int, int, datetime.date]]:
    """Return ``(matchday, home, away, date)`` for a double round robin.

    The circle method rotates every club except the first past the others, then
    the same rounds are replayed with home and away swapped.
    """
    rotation = list(range(len(team_ids)))
    fixtures: list[tuple[int, int, int, datetime.date]] = []
    matchday = 0
    for leg in (0, 1):
        for _ in range(len(team_ids) - 1):
            matchday += 1
            for index in range(len(team_ids) // 2):
                home, away = rotation[index], rotation[-1 - index]
                if leg:
                    home, away = away, home
                fixtures.append(
                    (matchday, team_ids[home], team_ids[away], _matchday_date(matchday))
                )
            rotation = [rotation[0], rotation[-1], *rotation[1:-1]]
    assert matchday == N_MATCHDAYS
    return fixtures


def _store_season(db: MigratedDatabase, team_ids: list[int]) -> None:
    """Store the whole invented season as played matches with matchdays."""
    season_id = _season_id(db)
    rows = []
    for matchday, home, away, day in _fixtures(team_ids):
        home_goals, away_goals = _scoreline(matchday)
        rows.append(
            {
                "season_id": season_id,
                "kickoff_date": day,
                "home_team_id": home,
                "away_team_id": away,
                "matchday": matchday,
                "status": "played",
                "home_goals": home_goals,
                "away_goals": away_goals,
                "result": _result(home_goals, away_goals),
            }
        )
    with db.factories.begin() as session:
        session.execute(insert(Match), rows)


def _table_rows(printed: str) -> list[tuple[int, str, float, list[float]]]:
    """Parse the printed projected table into ``(rank, name, points, probabilities)``."""
    rows: list[tuple[int, str, float, list[float]]] = []
    for line in printed.splitlines():
        fields = line.split()
        if not fields or not fields[0].isdigit():
            continue
        numbers = fields[-5:]
        rows.append(
            (
                int(fields[0]),
                " ".join(fields[1:-5]),
                float(numbers[0]),
                [float(value) for value in numbers[1:]],
            )
        )
    return rows


def _stored_runs(db: MigratedDatabase, *, kind: str | None = None) -> list[SimulationRun]:
    """Return the stored runs of the invented season, oldest first."""
    statement = (
        select(SimulationRun)
        .join(Season, Season.id == SimulationRun.season_id)
        .where(Season.code == SEASON_CODE)
        .order_by(SimulationRun.requested_at)
    )
    if kind is not None:
        statement = statement.where(SimulationRun.kind == kind)
    with db.factories() as session:
        return list(session.scalars(statement))


def _team_rows(db: MigratedDatabase, run_id: object) -> list[SimulationTeamResult]:
    """Return one run's stored team rows in projected table order."""
    statement = (
        select(SimulationTeamResult)
        .join(Team, Team.id == SimulationTeamResult.team_id)
        .where(SimulationTeamResult.run_id == run_id)
        .order_by(
            SimulationTeamResult.mean_position,
            SimulationTeamResult.mean_points.desc(),
            Team.name,
        )
    )
    with db.factories() as session:
        return list(session.scalars(statement))


def _run_arguments(*, as_of: datetime.date) -> list[str]:
    """Return the command line of one live run."""
    return [
        "simulate",
        "--season",
        SEASON_CODE,
        "--runs",
        str(N_RUNS),
        "--seed",
        str(SEED),
        "--as-of",
        as_of.isoformat(),
    ]


@pytest.mark.integration
def test_a_live_run_prints_the_table_and_stores_one_succeeded_run(
    migrated_engine: MigratedDatabase, capsys: pytest.CaptureFixture[str]
) -> None:
    team_ids = _team_ids(migrated_engine)
    _store_season(migrated_engine, team_ids)
    as_of = _matchday_date(8)

    code = main(_run_arguments(as_of=as_of))

    assert code == 0
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert lines[0] == f"season {SEASON_CODE} as of {as_of.isoformat()}"
    assert lines[1] == f"runs {N_RUNS} seed {SEED}"
    # Every played match before the as-of date is fixed; every match from the
    # as-of date on is re-simulated even though it is stored as played.
    assert lines[2] == (
        f"matches fixed {FIXED_AT_MID_SEASON} simulated {SIMULATED_AT_MID_SEASON} "
        "withdrawn excluded 0"
    )
    assert lines[3].startswith("duration ")
    assert lines[4].startswith("data fingerprint ")
    assert lines[6].split()[0] == "rank"

    rows = _table_rows(printed)
    assert [rank for rank, _, _, _ in rows] == list(range(1, N_TEAMS + 1))
    for _, _, mean_points, probabilities in rows:
        assert mean_points >= 0
        assert all(0.0 <= value <= 1.0 for value in probabilities)

    runs = _stored_runs(migrated_engine)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "succeeded"
    assert run.trigger == "cli"
    assert run.kind == "live"
    assert run.model == "dixon_coles"
    assert run.as_of_date == as_of
    assert run.n_runs == N_RUNS
    assert run.seed == SEED
    assert run.n_fixed == FIXED_AT_MID_SEASON
    assert run.n_simulated == SIMULATED_AT_MID_SEASON
    assert run.n_withdrawn_excluded == 0
    assert run.last_complete_matchday == 7
    assert run.duration_ms is not None
    assert run.finished_at is not None
    assert run.data_fingerprint
    assert run.model_version
    assert run.error is None

    stored = _team_rows(migrated_engine, run.id)
    assert len(stored) == N_TEAMS
    assert {row.team_id for row in stored} == set(team_ids)
    for row in stored:
        assert len(row.position_probs) == N_TEAMS
        assert pytest.approx(1.0, abs=1e-9) == sum(row.position_probs)
        assert pytest.approx(row.position_probs[0]) == row.p_title
        assert pytest.approx(sum(row.position_probs[:4])) == row.p_ucl
        assert pytest.approx(sum(row.position_probs[:6])) == row.p_europe
        assert pytest.approx(sum(row.position_probs[-3:])) == row.p_relegation
        # The fixed table of seven matchdays is the current standing, not a guess.
        assert row.current_played == 7
        # The pmf covers every total the team reached, so it starts at the
        # minimum and sums to one over the simulated seasons.
        assert row.points_pmf[0] > 0.0
        assert pytest.approx(1.0, abs=1e-9) == sum(row.points_pmf)
        assert row.points_p05 <= row.points_p95

    # The projection is one title across the league, and the printed order is
    # the stored projected order.
    assert pytest.approx(1.0, abs=1e-9) == sum(row.p_title for row in stored)
    assert [name for _, name, _, _ in rows] != []
    with migrated_engine.factories() as session:
        printed_names = [name for _, name, _, _ in rows]
        stored_names = [
            session.scalar(select(Team.name).where(Team.id == row.team_id)) for row in stored
        ]
    assert printed_names == stored_names


def _without_duration(printed: str) -> list[str]:
    """Return the printed table without the one line that measures wall time."""
    return [line for line in printed.splitlines() if not line.startswith("duration ")]


@pytest.mark.integration
def test_the_same_arguments_twice_store_identical_team_rows(
    migrated_engine: MigratedDatabase, capsys: pytest.CaptureFixture[str]
) -> None:
    team_ids = _team_ids(migrated_engine)
    _store_season(migrated_engine, team_ids)
    as_of = _matchday_date(8)

    first = main(_run_arguments(as_of=as_of))
    first_print = capsys.readouterr().out
    second = main(_run_arguments(as_of=as_of))
    second_print = capsys.readouterr().out

    assert first == 0
    assert second == 0
    assert _without_duration(first_print) == _without_duration(second_print)

    runs = _stored_runs(migrated_engine)
    assert [run.status for run in runs] == ["succeeded", "succeeded"]
    assert runs[0].id != runs[1].id
    with migrated_engine.factories() as session:
        differing = session.execute(
            text(DEFINITIVE_ROW), {"a": runs[0].id, "b": runs[1].id}
        ).scalar_one()
    assert differing == 0
    assert len(_team_rows(migrated_engine, runs[0].id)) == N_TEAMS


@pytest.mark.integration
def test_a_second_backfill_adds_no_run(
    migrated_engine: MigratedDatabase, capsys: pytest.CaptureFixture[str]
) -> None:
    team_ids = _team_ids(migrated_engine)
    _store_season(migrated_engine, team_ids)
    expected = {FIRST_MATCHDAY - datetime.timedelta(days=1)}
    expected.update(
        _matchday_date(matchday) + datetime.timedelta(days=1) for matchday in range(1, 15)
    )
    expected_dates = sorted(expected)

    arguments = ["simulate", "--season", SEASON_CODE, "--runs", str(N_RUNS)]
    arguments += ["--seed", str(SEED), "--backfill"]

    first = main(arguments)
    first_print = capsys.readouterr().out
    assert first == 0
    first_lines = first_print.splitlines()
    assert len(first_lines) == len(expected_dates)
    assert [line.split()[0] for line in first_lines] == [day.isoformat() for day in expected_dates]
    assert all(line.endswith("succeeded") for line in first_lines)

    stored = _stored_runs(migrated_engine, kind="backfill")
    assert [run.as_of_date for run in stored] == expected_dates
    assert all(run.status == "succeeded" for run in stored)

    # The final date is the day after the last kickoff, so nothing is left to
    # simulate and the stored positions are the real table.
    final = stored[-1]
    assert final.n_simulated == 0
    assert final.n_fixed == N_MATCHDAYS * (N_TEAMS // 2)
    one_hot = _team_rows(migrated_engine, final.id)
    assert len(one_hot) == N_TEAMS
    for row in one_hot:
        assert sum(1 for value in row.position_probs if value == 1.0) == 1

    second = main(arguments)
    second_print = capsys.readouterr().out
    assert second == 0
    second_lines = second_print.splitlines()
    assert len(second_lines) == len(expected_dates)
    assert all(line.endswith("skipped") for line in second_lines)
    assert [run.id for run in _stored_runs(migrated_engine, kind="backfill")] == [
        run.id for run in stored
    ]
