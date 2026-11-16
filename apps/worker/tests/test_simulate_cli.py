"""Tests for the ``simulate`` subcommand of the worker CLI.

The tests need no database and no network. The engine, the session factory, the
service functions and the clock are replaced by stands whose calls are recorded,
so only the command itself is exercised. The table helper is tested from a fixed
set of rows, and the backfill test runs the command twice to show that the second
invocation adds no run.

Every value here is synthetic: the team names, the dates and the probabilities
are invented and carry no real club, league or odds data.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterator
from uuid import UUID

import pytest
from lmi_core.db.models import SimulationRun, SimulationTeamResult
from lmi_core.simulation import DEFAULT_SEED, Qualification
from lmi_core.simulation.service import MODEL_NAME
from lmi_worker import simulate
from lmi_worker.cli import build_parser
from lmi_worker.simulate import TableRow, format_table, run_simulate_command

_SEASON = "2025-26"
_AS_OF = datetime.date(2026, 1, 15)
"""The live as-of date every explicit test passes, so no test reads the clock."""

_RANK_WIDTH = 4
_TEAM_WIDTH = 24
_POINTS_WIDTH = 11
_PROBABILITY_WIDTH = 12


class _EngineStand:
    """A stand-in engine, so no test opens a connection."""

    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class _SessionStand:
    """A stand-in session backed by the world's runs and team rows."""

    def __init__(self, world: _World) -> None:
        self.world = world
        self.expired = False

    def __enter__(self) -> _SessionStand:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def expire_all(self) -> None:
        self.expired = True

    def get(self, model: type[object], key: UUID) -> SimulationRun | None:
        return self.world.runs.get(key)

    def execute(self, statement: object) -> Iterator[tuple[SimulationTeamResult, str]]:
        return iter(self.world.team_rows)

    def scalars(self, statement: object) -> Iterator[SimulationRun]:
        return iter(
            run
            for run in self.world.runs.values()
            if run.kind == "backfill" and run.status == "succeeded"
        )


class _World:
    """The recorded collaborators of one command invocation."""

    def __init__(self) -> None:
        self.engine = _EngineStand()
        self.session = _SessionStand(self)
        self.runs: dict[UUID, SimulationRun] = {}
        self.team_rows: list[tuple[SimulationTeamResult, str]] = []
        self.dates: list[datetime.date] = []
        self.created: list[dict[str, object]] = []
        self.executed: list[UUID] = []
        self.next_id = 1
        self.create_error: str | None = None
        self.execute_error: str | None = None
        self.dates_error: str | None = None

    def build_engine(self, settings: object) -> _EngineStand:
        return self.engine

    def build_session_factory(self, engine: object) -> Callable[[], _SessionStand]:
        return lambda: self.session

    def create_run(
        self,
        session: object,
        *,
        season_code: str,
        as_of_date: datetime.date,
        n_runs: int,
        seed: int,
        trigger: str,
        qualification: Qualification,
        kind: str = "live",
    ) -> tuple[SimulationRun, bool]:
        if self.create_error is not None:
            raise ValueError(self.create_error)
        self.created.append(
            {
                "season_code": season_code,
                "as_of_date": as_of_date,
                "n_runs": n_runs,
                "seed": seed,
                "trigger": trigger,
                "qualification": qualification,
                "kind": kind,
            }
        )
        return self.make_run(
            as_of_date=as_of_date,
            n_runs=n_runs,
            seed=seed,
            trigger=trigger,
            qualification=qualification,
            kind=kind,
        ), True

    def execute_run(self, factory: object, run_id: UUID) -> None:
        self.executed.append(run_id)
        run = self.runs[run_id]
        if self.execute_error is not None:
            run.status = "failed"
            run.error = self.execute_error
            return
        run.status = "succeeded"
        run.n_fixed = 190
        run.n_simulated = 190
        run.n_withdrawn_excluded = 0
        run.duration_ms = 1500
        run.data_fingerprint = "0123456789abcdef"

    def backfill_dates(self, session: object, season_code: str) -> list[datetime.date]:
        if self.dates_error is not None:
            raise ValueError(self.dates_error)
        return list(self.dates)

    def make_run(
        self,
        *,
        as_of_date: datetime.date,
        n_runs: int = 200,
        seed: int = 3,
        trigger: str = "cli",
        qualification: Qualification | None = None,
        kind: str = "live",
    ) -> SimulationRun:
        positions = qualification or Qualification()
        run = SimulationRun(
            id=UUID(int=self.next_id),
            season_id=1,
            as_of_date=as_of_date,
            n_runs=n_runs,
            seed=seed,
            model=MODEL_NAME,
            ucl_positions=positions.ucl_positions,
            europe_positions=positions.europe_positions,
            relegation_positions=positions.relegation_positions,
            kind=kind,
            trigger=trigger,
            status="queued",
            code_version="test",
        )
        self.next_id += 1
        self.runs[run.id] = run
        return run

    def add_succeeded_backfill(self, day: datetime.date) -> SimulationRun:
        run = self.make_run(as_of_date=day, kind="backfill")
        run.status = "succeeded"
        return run


def _install(monkeypatch: pytest.MonkeyPatch, world: _World, *, today: datetime.date) -> _World:
    """Replace every collaborator the command looks up at call time."""
    monkeypatch.setattr(simulate, "make_engine", world.build_engine)
    monkeypatch.setattr(simulate, "session_factory", world.build_session_factory)
    monkeypatch.setattr(simulate, "create_run", world.create_run)
    monkeypatch.setattr(simulate, "execute_run", world.execute_run)
    monkeypatch.setattr(simulate, "backfill_dates", world.backfill_dates)
    monkeypatch.setattr(simulate, "madrid_today", lambda: today)
    return world


def _team_result(
    team_id: int,
    *,
    mean_points: float,
    mean_position: float,
    p_title: float,
    p_ucl: float,
    p_europe: float,
    p_relegation: float,
) -> SimulationTeamResult:
    """Return one stored team row with the fields the table reads."""
    return SimulationTeamResult(
        run_id=UUID(int=1),
        team_id=team_id,
        current_played=0,
        current_points=0,
        current_goal_difference=0,
        mean_points=mean_points,
        points_p05=0,
        points_p25=0,
        points_p50=0,
        points_p75=0,
        points_p95=0,
        points_min=0,
        points_pmf=[1.0],
        mean_position=mean_position,
        position_probs=[1.0],
        p_title=p_title,
        p_ucl=p_ucl,
        p_europe=p_europe,
        p_relegation=p_relegation,
    )


def test_the_parser_accepts_every_documented_option() -> None:
    args = build_parser().parse_args(
        [
            "simulate",
            "--season",
            _SEASON,
            "--as-of",
            "2026-01-15",
            "--runs",
            "200",
            "--seed",
            "3",
        ]
    )

    assert args.season == _SEASON
    assert args.as_of == _AS_OF
    assert args.runs == 200
    assert args.seed == 3
    assert args.backfill is False

    backfill = build_parser().parse_args(["simulate", "--backfill"])
    assert backfill.backfill is True


def test_the_parser_defaults_follow_the_documented_values() -> None:
    args = build_parser().parse_args(["simulate"])

    assert args.season is None
    assert args.as_of is None
    assert args.runs == 10000
    assert args.seed == DEFAULT_SEED
    assert args.backfill is False


@pytest.mark.parametrize(
    "argv",
    [
        ["simulate", "--runs", "99"],
        ["simulate", "--runs", "100001"],
        ["simulate", "--seed", "-1"],
        ["simulate", "--as-of", "not-a-date"],
    ],
)
def test_an_out_of_range_value_exits_two(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(argv)

    assert excinfo.value.code == 2
    assert capsys.readouterr().err.strip() != ""


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (datetime.date(2025, 6, 30), "2024-25"),
        (datetime.date(2025, 7, 1), "2025-26"),
    ],
)
def test_the_default_season_follows_the_july_boundary(
    today: datetime.date,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _World()
    _install(monkeypatch, world, today=today)

    code = run_simulate_command(build_parser().parse_args(["simulate"]))

    assert code == 0
    assert capsys.readouterr().err == ""
    assert world.created[0]["season_code"] == expected
    assert world.created[0]["as_of_date"] == today


def test_an_unknown_season_code_exits_two_and_names_the_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _World()
    world.create_error = "unknown season code '2020-21'"
    _install(monkeypatch, world, today=_AS_OF)

    code = run_simulate_command(
        build_parser().parse_args(["simulate", "--season", "2020-21", "--as-of", "2026-01-15"])
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "2020-21" in captured.err


def test_an_unknown_season_code_stops_the_backfill_before_the_first_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _World()
    world.dates_error = "unknown season code '2020-21'"
    _install(monkeypatch, world, today=_AS_OF)

    code = run_simulate_command(
        build_parser().parse_args(["simulate", "--season", "2020-21", "--backfill"])
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "2020-21" in captured.err
    assert world.created == []


def test_a_live_run_prints_the_table_and_records_the_cli_trigger(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _World()
    world.team_rows = [
        (
            _team_result(
                1,
                mean_points=62.4,
                mean_position=1.0,
                p_title=0.35,
                p_ucl=0.65,
                p_europe=0.8,
                p_relegation=0.01,
            ),
            "Aurora FC",
        )
    ]
    _install(monkeypatch, world, today=_AS_OF)

    code = run_simulate_command(
        build_parser().parse_args(
            [
                "simulate",
                "--season",
                _SEASON,
                "--as-of",
                "2026-01-15",
                "--runs",
                "200",
                "--seed",
                "3",
            ]
        )
    )

    captured = capsys.readouterr()
    assert code == 0
    assert world.engine.disposed is True
    assert world.executed == [UUID(int=1)]
    assert world.created[0]["trigger"] == "cli"
    assert world.created[0]["kind"] == "live"
    assert world.created[0]["qualification"] == Qualification()
    assert "season 2025-26 as of 2026-01-15" in captured.out
    assert "Aurora FC" in captured.out


def test_a_failed_stored_run_exits_one_and_names_its_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _World()
    world.execute_error = "no played match exists before the as-of date"
    _install(monkeypatch, world, today=_AS_OF)

    code = run_simulate_command(
        build_parser().parse_args(["simulate", "--season", _SEASON, "--as-of", "2026-01-15"])
    )

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "no played match exists before the as-of date" in captured.err


def test_the_table_renders_the_projected_order_at_four_decimals() -> None:
    run = _World().make_run(as_of_date=_AS_OF)
    run.status = "succeeded"
    run.n_fixed = 190
    run.n_simulated = 190
    run.n_withdrawn_excluded = 2
    run.duration_ms = 1500
    run.data_fingerprint = "0123456789abcdef"
    rows = [
        TableRow("Aurora FC", 1, 62.4, 0.35, 0.65, 0.8, 0.01),
        TableRow("Borealis United", 2, 58.0, 0.1, 0.4, 0.55, 0.02),
        TableRow("Cinder Town", 3, 40.1, 0.0, 0.01, 0.03, 0.42),
    ]

    expected = "\n".join(
        [
            "season 2025-26 as of 2026-01-15",
            "runs 200 seed 3",
            "matches fixed 190 simulated 190 withdrawn excluded 2",
            "duration 1500 ms",
            "data fingerprint 0123456789abcdef",
            "",
            _expected_header(),
            _expected_line(1, "Aurora FC", 62.4, 0.35, 0.65, 0.8, 0.01),
            _expected_line(2, "Borealis United", 58.0, 0.1, 0.4, 0.55, 0.02),
            _expected_line(3, "Cinder Town", 40.1, 0.0, 0.01, 0.03, 0.42),
        ]
    )

    text = format_table(season_code=_SEASON, run=run, rows=rows)

    assert text == expected
    assert text.splitlines()[6].split()[:2] == ["rank", "team"]
    assert format_table(season_code=_SEASON, run=run, rows=rows) == text


def test_a_backfill_skips_a_stored_date_and_a_second_invocation_adds_no_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _World()
    world.dates = [
        datetime.date(2025, 8, 10),
        datetime.date(2025, 8, 17),
        datetime.date(2025, 8, 24),
    ]
    prior = world.add_succeeded_backfill(world.dates[0])
    _install(monkeypatch, world, today=_AS_OF)
    arguments = [
        "simulate",
        "--season",
        _SEASON,
        "--as-of",
        "2026-01-15",
        "--runs",
        "200",
        "--seed",
        "3",
        "--backfill",
    ]

    code = run_simulate_command(build_parser().parse_args(arguments))

    first = capsys.readouterr().out.splitlines()
    assert code == 0
    assert world.engine.disposed is True
    assert len(world.created) == 2
    assert [entry["kind"] for entry in world.created] == ["backfill", "backfill"]
    assert first[0] == f"2025-08-10 {prior.id} skipped"
    assert first[1].split()[0] == "2025-08-17"
    assert first[1].endswith(" succeeded")
    assert first[2].split()[0] == "2025-08-24"
    assert first[2].endswith(" succeeded")

    code = run_simulate_command(build_parser().parse_args(arguments))

    second = capsys.readouterr().out.splitlines()
    assert code == 0
    assert len(world.created) == 2
    assert len(world.executed) == 2
    assert [line.split()[0] for line in second] == [
        "2025-08-10",
        "2025-08-17",
        "2025-08-24",
    ]
    assert all(line.endswith(" skipped") for line in second)


def _expected_header() -> str:
    """Return the exact header line, with the widths the contract fixes."""
    return (
        f"{'rank':>{_RANK_WIDTH}} {'team':<{_TEAM_WIDTH}} "
        f"{'mean_points':>{_POINTS_WIDTH}} {'p_title':>{_PROBABILITY_WIDTH}} "
        f"{'p_ucl':>{_PROBABILITY_WIDTH}} {'p_europe':>{_PROBABILITY_WIDTH}} "
        f"{'p_relegation':>{_PROBABILITY_WIDTH}}"
    )


def _expected_line(
    rank: int,
    team_name: str,
    mean_points: float,
    p_title: float,
    p_ucl: float,
    p_europe: float,
    p_relegation: float,
) -> str:
    """Return the exact table line for one hand-built row."""
    return (
        f"{rank:>{_RANK_WIDTH}} {team_name:<{_TEAM_WIDTH}} "
        f"{mean_points:>{_POINTS_WIDTH}.1f} {p_title:>{_PROBABILITY_WIDTH}.4f} "
        f"{p_ucl:>{_PROBABILITY_WIDTH}.4f} {p_europe:>{_PROBABILITY_WIDTH}.4f} "
        f"{p_relegation:>{_PROBABILITY_WIDTH}.4f}"
    )


def test_the_backfill_exits_one_when_one_attempted_run_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _World()
    world.dates = [datetime.date(2025, 8, 10)]
    world.execute_error = "the model needs a played match"
    _install(monkeypatch, world, today=_AS_OF)

    code = run_simulate_command(
        build_parser().parse_args(
            ["simulate", "--season", _SEASON, "--as-of", "2026-01-15", "--backfill"]
        )
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "2025-08-10" in captured.out
    assert captured.out.strip().endswith("failed")
    assert "the model needs a played match" in captured.err
