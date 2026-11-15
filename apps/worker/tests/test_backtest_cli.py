"""Tests for the ``backtest`` subcommand of the worker CLI.

The unit tests need no database and no network. They pin the command line, the
refusal before the first write, the scored-range count, the rendered table and
the fact that ``--format json`` writes nothing but the document. The engine, the
session, the loader and the store are replaced by stands, so only the command
itself is exercised.

Every synthetic history here is hand-built from NumPy arrays: the core test
helpers live in another package and are not importable from this one, and the
values the assertions compare are the ones the arrays were built from.

The one integration test runs the stored path against the migrated disposable
database and then the ``--no-save --format json`` path twice, which is the
reproducibility check of the brief: the same data and the same configuration
must produce the same metrics, calibration and fingerprint.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import logging
import sys
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING
from uuid import UUID

import numpy as np
import pytest
from lmi_core.db.models import BacktestMetric, BacktestRun, Season
from lmi_core.evaluation.backtest import run_backtest
from lmi_core.evaluation.report import BacktestConfig
from lmi_core.modeling.registry import MODEL_NAMES
from lmi_core.modeling.types import MatchData
from lmi_core.results.schemas import (
    BacktestRunOut,
    CalibrationBin,
    MetricRow,
    ModelInfo,
    ModelMetricsResponse,
)
from lmi_worker import backtest
from lmi_worker.backtest import format_table, run_backtest_command, scored_match_count
from lmi_worker.cli import build_parser
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from conftest import MigratedDatabase
    from sqlalchemy.orm import Session

_MATCHES_PER_WEEK = 10
_WEEKS = 19
"""A season of 190 matches on weekly matchdays; two of them clear the minimum."""

_SCORELINES = ((2, 0), (1, 1), (0, 2))
"""One scoreline per outcome index, so ``result`` follows the goals by construction."""

_METRIC_MODELS = ("home_win", "base_rate")

_SMALL_SEASON = 1
_SMALL_CODE = "2007-08"


class _EngineStand:
    """A stand-in engine, so no test opens a connection."""

    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class _SessionStand:
    """A stand-in session that answers the season query with fixed rows."""

    def __init__(self, season_rows: Sequence[tuple[int, str]] = ()) -> None:
        self.season_rows = list(season_rows)

    def __enter__(self) -> _SessionStand:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def execute(self, statement: object) -> list[tuple[int, str]]:
        return self.season_rows

    def rollback(self) -> None:
        """Do nothing: the stand holds no transaction to roll back."""


def _synthetic_history(seasons: Sequence[tuple[int, int]]) -> MatchData:
    """Return played matches for each ``(season_id, start_year)`` pair.

    A season is a block of weekly matchdays with alternating results, sorted by
    ``(kickoff_date, match_id)`` as :class:`~lmi_core.modeling.types.History`
    requires. No row carries odds, which is the coverage case the table reports.
    """
    match_ids: list[int] = []
    kickoffs: list[datetime.date] = []
    season_ids: list[int] = []
    home_ids: list[int] = []
    away_ids: list[int] = []
    home_goals: list[int] = []
    away_goals: list[int] = []
    results: list[int] = []
    for season_id, start_year in seasons:
        first = datetime.date(start_year, 8, 18)
        for week in range(_WEEKS):
            day = first + datetime.timedelta(days=7 * week)
            for slot in range(_MATCHES_PER_WEEK):
                outcome = (len(match_ids) + 1) % len(_SCORELINES)
                match_ids.append(len(match_ids) + 1)
                kickoffs.append(day)
                season_ids.append(season_id)
                home_ids.append(slot + 1)
                away_ids.append((slot + _MATCHES_PER_WEEK // 2) % _MATCHES_PER_WEEK + 1)
                home_goals.append(_SCORELINES[outcome][0])
                away_goals.append(_SCORELINES[outcome][1])
                results.append(outcome)
    n = len(match_ids)
    return MatchData(
        match_id=np.array(match_ids, dtype=np.int64),
        kickoff_date=np.array(kickoffs, dtype="datetime64[D]"),
        season_id=np.array(season_ids, dtype=np.int64),
        home_team_id=np.array(home_ids, dtype=np.int64),
        away_team_id=np.array(away_ids, dtype=np.int64),
        odds=np.full((n, 3), np.nan),
        odds_source=tuple(None for _ in range(n)),
        home_goals=np.array(home_goals, dtype=np.int64),
        away_goals=np.array(away_goals, dtype=np.int64),
        result=np.array(results, dtype=np.int8),
    )


def _small_history() -> MatchData:
    """Return four played matches of a season starting in 2007, below the minimum."""
    return MatchData(
        match_id=np.arange(1, 5, dtype=np.int64),
        kickoff_date=np.array(
            ["2007-08-18", "2007-08-25", "2007-09-01", "2007-09-08"],
            dtype="datetime64[D]",
        ),
        season_id=np.full(4, _SMALL_SEASON, dtype=np.int64),
        home_team_id=np.array([1, 2, 3, 1], dtype=np.int64),
        away_team_id=np.array([2, 3, 1, 2], dtype=np.int64),
        odds=np.full((4, 3), np.nan),
        odds_source=("pinnacle", None, "bet365", "market_avg"),
        home_goals=np.array([2, 1, 0, 3], dtype=np.int64),
        away_goals=np.array([0, 1, 2, 3], dtype=np.int64),
        result=np.array([0, 1, 2, 1], dtype=np.int8),
    )


def _install_stands(
    monkeypatch: pytest.MonkeyPatch,
    *,
    data: MatchData,
    season_rows: Sequence[tuple[int, str]] = (),
) -> tuple[_EngineStand, list[dict[str, object]]]:
    """Replace the engine, the session factory, the loader and ``start_run``."""
    engine = _EngineStand()
    session = _SessionStand(season_rows)
    started: list[dict[str, object]] = []

    def build_engine(settings: object) -> _EngineStand:
        return engine

    def build_session_factory(other: object) -> Callable[[], _SessionStand]:
        return lambda: session

    def load(other: object) -> MatchData:
        return data

    def start(
        other: object,
        *,
        seed: int,
        from_year: int,
        step_days: int,
        models: object,
    ) -> UUID:
        started.append({"seed": seed, "from_year": from_year, "step_days": step_days})
        return UUID(int=1)

    monkeypatch.setattr(backtest, "make_engine", build_engine)
    monkeypatch.setattr(backtest, "session_factory", build_session_factory)
    monkeypatch.setattr(backtest, "load_match_data", load)
    monkeypatch.setattr(backtest, "start_run", start)
    return engine, started


@contextlib.contextmanager
def _progress_lines() -> Iterator[None]:
    """Send the module's INFO progress lines to the live stderr.

    ``python -m lmi_worker`` configures the root logger; a test process does
    not, so the handler is attached here. It is built inside the test body on
    purpose: pytest swaps ``sys.stderr`` per test phase, and a handler built
    during setup would hold a stream the call phase has already closed.
    """
    target = logging.getLogger("lmi_worker.backtest")
    handler = logging.StreamHandler(sys.stderr)
    target.addHandler(handler)
    target.setLevel(logging.INFO)
    try:
        yield
    finally:
        target.removeHandler(handler)
        target.setLevel(logging.NOTSET)


def test_the_subcommand_defaults_follow_the_documented_values() -> None:
    args = build_parser().parse_args(["backtest"])

    assert args.models == MODEL_NAMES
    assert args.from_year == 2007
    assert args.step_days == 7
    assert args.seed == 42
    assert args.format == "table"
    assert args.no_save is False


def test_an_unknown_model_exits_two_and_names_the_known_ones(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["backtest", "--models", "home_win,foobar"])

    assert excinfo.value.code == 2
    error = capsys.readouterr().err
    for name in MODEL_NAMES:
        assert name in error


def test_a_repeated_model_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["backtest", "--models", "home_win,home_win"])

    assert excinfo.value.code == 2
    error = capsys.readouterr().err
    assert "home_win" in error
    for name in MODEL_NAMES:
        assert name in error


def test_a_range_below_the_minimum_is_refused_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine, started = _install_stands(monkeypatch, data=_small_history())

    code = run_backtest_command(build_parser().parse_args(["backtest"]))

    captured = capsys.readouterr()
    assert code == 1
    assert started == []
    assert engine.disposed is True
    assert captured.out == ""
    assert (
        "backtest: refusing to run: 4 played matches in the scored range, "
        "at least 380 are required" in captured.err
    )


def test_scored_match_count_keeps_the_seasons_at_or_after_the_boundary() -> None:
    data = _synthetic_history([(1, 2005), (2, 2006)])

    assert scored_match_count(data, 2005) == 380
    assert scored_match_count(data, 2006) == 190
    assert scored_match_count(data, 2007) == 0


def test_the_table_carries_the_header_the_counts_and_the_clip_note() -> None:
    response = _response()
    data = _small_history()

    text = format_table(response, data=data, season_codes={_SMALL_SEASON: _SMALL_CODE})
    lines = text.splitlines()

    assert "seed 42" in lines
    assert "data fingerprint 0123456789abcdef" in lines
    assert "code version 0.1.0" in lines
    assert f"scored seasons {_SMALL_CODE}" in lines
    assert "scored matches 4" in lines

    coverage = next(line for line in lines if line.startswith(f"season {_SMALL_CODE} pinnacle"))
    assert coverage.split()[2:] == [
        "pinnacle=1",
        "market_avg=1",
        "bet365=1",
        "none=1",
    ]

    season_start = lines.index(f"season {_SMALL_CODE} common")
    assert lines[season_start + 2].split() == [
        "home_win",
        "2",
        "0.500000",
        "1.250000",
        "0.750000",
        "0.375000",
        "0.500000",
    ]

    overall_start = lines.index("overall all")
    assert lines[overall_start + 2].split() == [
        "home_win",
        "4",
        "1.000000",
        "1.500000",
        "0.500000",
        "0.250000",
        "1.000000",
    ]
    assert lines[overall_start + 3].split() == [
        "base_rate",
        "4",
        "1.000000",
        "0.937500",
        "0.562500",
        "0.062500",
        "0.500000",
    ]

    calibration_start = lines.index("calibration expected error")
    assert lines[calibration_start + 2].split() == [
        "home_win",
        "0.050000",
        "0.030000",
        "0.000000",
    ]

    note = lines[-1]
    assert note.startswith("note:")
    assert "home_win" in note
    assert "1e-15" in note

    again = format_table(response, data=data, season_codes={_SMALL_SEASON: _SMALL_CODE})
    assert again == text


def test_a_season_without_a_common_row_says_so_instead_of_showing_a_header() -> None:
    response = _season_without_a_common_row()
    data = _small_history()

    lines = format_table(
        response, data=data, season_codes={_SMALL_SEASON: _SMALL_CODE}
    ).splitlines()

    season_start = lines.index(f"season {_SMALL_CODE} common")
    assert lines[season_start + 2] == "no model has a forecast in this scope"
    # Nothing invented follows the line: the next block starts right after the
    # blank separator, so no numeric row sits under the short explanation.
    assert lines[season_start + 3] == ""
    assert lines[season_start + 4] == "overall all"


def test_a_failure_after_the_run_starts_marks_it_failed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = _synthetic_history([(1, 2007), (2, 2008)])
    _install_stands(monkeypatch, data=data, season_rows=[(1, "2007-08"), (2, "2008-09")])
    failed: list[dict[str, object]] = []

    def broken_season_codes(session: object) -> dict[int, str]:
        raise SQLAlchemyError("gone")

    def fail(other: object, run_id: UUID, *, error: str) -> None:
        failed.append({"run_id": run_id, "error": error})

    # Every step after start_run is under the same guard, so a failure below it
    # must both exit non-zero and leave no run behind as running.
    monkeypatch.setattr(backtest, "_season_codes", broken_season_codes)
    monkeypatch.setattr(backtest, "fail_run", fail)

    argv = ["backtest", "--models", ",".join(_METRIC_MODELS), "--step-days", "7"]
    code = run_backtest_command(build_parser().parse_args(argv))

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert [entry["run_id"] for entry in failed] == [UUID(int=1)]
    assert "gone" in str(failed[0]["error"])


def test_json_output_is_the_only_thing_on_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = _synthetic_history([(1, 2007), (2, 2008)])
    config = BacktestConfig(models=_METRIC_MODELS, from_year=2007, step_days=7, seed=42)
    report = run_backtest(data, config)
    engine, started = _install_stands(
        monkeypatch,
        data=data,
        season_rows=[(1, "2007-08"), (2, "2008-09")],
    )

    def replay(match_data: MatchData, backtest_config: BacktestConfig) -> object:
        return report

    monkeypatch.setattr(backtest, "run_backtest", replay)

    argv = ["backtest", "--models", ",".join(_METRIC_MODELS), "--no-save", "--format", "json"]
    with _progress_lines():
        code = run_backtest_command(build_parser().parse_args(argv))

    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert code == 0
    assert engine.disposed is True
    assert started == []
    assert document["run"]["id"] is None
    assert document["run"]["data_fingerprint"] == report.data_fingerprint
    assert captured.err.strip() != ""


def _response() -> ModelMetricsResponse:
    """Return a hand-built contract with the values the table assertions compare."""
    models = [
        ModelInfo(name="home_win", kind="baseline", version="1", hyperparameters={}),
        ModelInfo(name="base_rate", kind="baseline", version="1", hyperparameters={}),
    ]
    metrics = [
        _metric("home_win", _SMALL_CODE, "common", 2, 4, 1.25, 0.75, 0.375, 0.5),
        _metric("home_win", None, "all", 4, 4, 1.5, 0.5, 0.25, 1.0),
        _metric("home_win", None, "common", 2, 4, 1.25, 0.75, 0.375, 0.5),
        _metric("base_rate", _SMALL_CODE, "common", 2, 4, 0.875, 0.625, 0.125, 0.5),
        _metric("base_rate", None, "all", 4, 4, 0.9375, 0.5625, 0.0625, 0.5),
        _metric("base_rate", None, "common", 2, 4, 0.875, 0.625, 0.125, 0.5),
    ]
    calibration = [
        _bin("home_win", "H", 9, 2, 0.95, 1.0),
        _bin("home_win", "D", 0, 2, 0.03, 0.0),
        _bin("home_win", "A", 0, 0, None, None),
        _bin("base_rate", "H", 5, 4, 0.5, 0.5),
    ]
    return ModelMetricsResponse(
        run=BacktestRunOut(
            id=None,
            started_at=None,
            finished_at=None,
            seed=42,
            from_year=2007,
            step_days=7,
            code_version="0.1.0",
            data_fingerprint="0123456789abcdef",
            n_matches=4,
        ),
        models=models,
        metrics=metrics,
        calibration=calibration,
    )


def _season_without_a_common_row() -> ModelMetricsResponse:
    """Return the hand-built contract with the overall rows only.

    The runner omits a row whose ``n_matches`` is zero, so a season without an
    eligible odds price carries no ``common`` row while the season still appears
    in the report through its ``all`` rows.
    """
    base = _response()
    metrics = [
        _metric("home_win", _SMALL_CODE, "all", 4, 4, 1.5, 0.5, 0.25, 1.0),
        _metric("base_rate", _SMALL_CODE, "all", 4, 4, 0.9375, 0.5625, 0.0625, 0.5),
        *(row for row in base.metrics if row.season_code is None),
    ]
    return base.model_copy(update={"metrics": metrics})


def _metric(
    model: str,
    season_code: str | None,
    subset: str,
    n_matches: int,
    n_fixtures: int,
    log_loss: float,
    brier: float,
    rps: float,
    accuracy: float,
) -> MetricRow:
    """Return one metric row whose coverage is exactly ``n_matches / n_fixtures``."""
    return MetricRow.model_validate(
        {
            "model": model,
            "season_code": season_code,
            "subset": subset,
            "n_matches": n_matches,
            "n_fixtures": n_fixtures,
            "coverage": n_matches / n_fixtures,
            "log_loss": log_loss,
            "brier": brier,
            "rps": rps,
            "accuracy": accuracy,
        }
    )


def _bin(
    model: str,
    outcome: str,
    bin_index: int,
    n: int,
    mean_predicted: float | None,
    observed_frequency: float | None,
) -> CalibrationBin:
    """Return one calibration bin of ten equal widths."""
    return CalibrationBin.model_validate(
        {
            "model": model,
            "outcome": outcome,
            "bin_index": bin_index,
            "bin_lower": bin_index / 10,
            "bin_upper": (bin_index + 1) / 10,
            "n": n,
            "mean_predicted": mean_predicted,
            "observed_frequency": observed_frequency,
        }
    )


def _lowest_seasons(database: MigratedDatabase) -> list[tuple[int, str, int]]:
    """Return the two lowest stored seasons as ``(id, code, start_year)``."""
    statement = select(Season.id, Season.code, Season.start_year).order_by(Season.id).limit(2)
    seasons: list[tuple[int, str, int]] = []
    with database.factories() as session:
        for row in session.execute(statement):
            seasons.append((int(row.id), str(row.code), int(row.start_year)))
    return seasons


@pytest.mark.integration
def test_the_save_path_stores_one_run_and_the_no_save_path_is_reproducible(
    migrated_engine: MigratedDatabase,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seasons = _lowest_seasons(migrated_engine)
    data = _synthetic_history([(season_id, start_year) for season_id, _, start_year in seasons])

    def load(other: object) -> MatchData:
        return data

    monkeypatch.setattr(backtest, "load_match_data", load)
    arguments = ["backtest", "--models", ",".join(_METRIC_MODELS), "--seed", "42"]
    arguments += ["--from-year", str(seasons[0][2])]

    code = run_backtest_command(build_parser().parse_args(arguments))

    printed = capsys.readouterr().out
    assert code == 0
    for _, season_code, _ in seasons:
        assert season_code in printed

    with migrated_engine.factories() as session:
        runs = list(session.scalars(select(BacktestRun)))
        assert [run.status for run in runs] == ["succeeded"]
        rows = list(
            session.scalars(select(BacktestMetric).where(BacktestMetric.run_id == runs[0].id))
        )
    stored_values = {
        "log_loss": [row.log_loss for row in rows],
        "brier": [row.brier for row in rows],
        "rps": [row.rps for row in rows],
        "accuracy": [row.accuracy for row in rows],
    }
    assert stored_values["log_loss"]
    # finish_run stores the digits the run computed: rounding one metric before
    # the write would still leave rows here, so each column on its own must keep
    # a value beyond the display precision of the printed table.
    for column, values in stored_values.items():
        assert any(value != round(value, backtest.DISPLAY_DIGITS) for value in values), column

    documents = []
    for _ in range(2):
        argv = [*arguments, "--no-save", "--format", "json"]
        assert run_backtest_command(build_parser().parse_args(argv)) == 0
        documents.append(json.loads(capsys.readouterr().out))

    assert documents[0]["metrics"] == documents[1]["metrics"]
    assert documents[0]["calibration"] == documents[1]["calibration"]
    assert documents[0]["run"]["data_fingerprint"] == documents[1]["run"]["data_fingerprint"]
    with migrated_engine.factories() as session:
        assert session.scalar(select(func.count()).select_from(BacktestRun)) == 1


@pytest.mark.integration
def test_a_database_error_after_the_run_starts_still_marks_it_failed(
    migrated_engine: MigratedDatabase,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seasons = _lowest_seasons(migrated_engine)
    data = _synthetic_history([(season_id, start_year) for season_id, _, start_year in seasons])

    def load(other: object) -> MatchData:
        return data

    def abort(session: Session) -> dict[int, str]:
        # The server rejects the statement, which aborts the transaction of the
        # session the command opened: the exact state in which the failure of
        # the run itself has to be written from a usable session again.
        session.execute(text("SELECT 1 / 0"))
        raise AssertionError("the server must reject the statement")

    # _season_codes runs on the session start_run wrote through, so the error it
    # raises leaves no clean transaction behind for the failed transition.
    monkeypatch.setattr(backtest, "load_match_data", load)
    monkeypatch.setattr(backtest, "_season_codes", abort)

    arguments = ["backtest", "--models", ",".join(_METRIC_MODELS)]
    arguments += ["--from-year", str(seasons[0][2])]

    code = run_backtest_command(build_parser().parse_args(arguments))

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    # A separate session, so the assertion reads what the command committed and
    # not what its own session still holds.
    with migrated_engine.factories() as session:
        runs = list(session.scalars(select(BacktestRun)))
        assert [run.status for run in runs] == ["failed"]
        assert runs[0].error is not None
