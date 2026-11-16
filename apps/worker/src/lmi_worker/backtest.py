"""The ``backtest`` subcommand: run the walk-forward backtest, print or store it.

The command is the only producer of a stored backtest run. It loads the played
matches once, runs :func:`lmi_core.evaluation.backtest.run_backtest` on them, and
turns the report into the shared results contract with
:func:`lmi_core.results.convert.report_to_response`. Nothing here computes a
metric, derives a coverage or invents a row: the runner, the converter and the
store own those rules, and the CLI only decides which of them to call.

Two paths share one body. With ``--no-save`` the contract is printed straight
from the report and carries a null run identity, so a reader can inspect a run
without writing a row. Without it the run is opened with
:func:`lmi_core.results.store.start_run`, finished with its metrics and
calibration, and then reloaded with
:func:`lmi_core.results.store.load_report`, so the printed output is the stored
report itself, identity included.

The refusal before the first write is deliberate: a backtest over a range with
too few played matches scores a number nobody can read, so the command stops
with exit code 1 before ``start_run`` instead of storing a useless run.

Once the run is open it is never left ``running``. The guard around it records
every failure on the run, and an interruption counts: ``KeyboardInterrupt`` and
``SystemExit`` are not ``Exception``, so they have their own branch, which
records them and then re-raises the original exception unchanged. An
interruption prints no line and returns no exit code, the traceback being the
report.

Only the selected format reaches stdout. Every progress line goes through
:data:`logger` at INFO, which ``python -m lmi_worker`` sends to stderr, so
``--format json`` stays a machine-readable document.

The module-level names the command calls are looked up at call time on purpose
(``make_engine``, ``load_match_data``, ``run_backtest`` and the store
functions), which is what lets the tests replace the database, the loader and
the runner without a live server.
"""

import argparse
import datetime
import logging
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Final

import numpy as np
from lmi_core.config import get_settings
from lmi_core.db.models import Season
from lmi_core.db.session import make_engine, session_factory
from lmi_core.evaluation.backtest import run_backtest
from lmi_core.evaluation.metrics import EPS
from lmi_core.evaluation.report import BacktestConfig
from lmi_core.modeling.data import load_match_data
from lmi_core.modeling.registry import MODEL_KINDS, MODEL_NAMES, make_model
from lmi_core.modeling.types import MatchData
from lmi_core.results.convert import report_to_response, round_response
from lmi_core.results.schemas import BacktestRunOut, MetricRow, ModelInfo, ModelMetricsResponse
from lmi_core.results.store import (
    fail_run,
    finish_run,
    installed_code_version,
    load_report,
    start_run,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MINIMUM_SCORED_MATCHES: Final = 380
"""Played matches a scored range must hold before the command runs anything."""

DEFAULT_FROM_YEAR = 2007
"""The first scored season, as a start year, when ``--from-year`` is absent."""

DEFAULT_STEP_DAYS = 7
"""The default walk-forward window in days."""

DEFAULT_SEED = 42
"""The default seed every model of the run is constructed with."""

DISPLAY_DIGITS = 6
"""The number of decimals the table and the JSON output show."""

_OUTCOMES: Final = ("H", "D", "A")
"""Calibration outcomes in the order models emit their probabilities."""

_ODDS_LABELS: Final = ("pinnacle", "market_avg", "bet365", "none")
"""The odds source labels the coverage section counts; ``none`` means no price."""

_METRIC_COLUMNS: Final = ("coverage", "log_loss", "brier", "rps", "accuracy")
"""The value columns every metric table shows, right aligned at display precision."""

_NO_DATA_LINE: Final = "no model has a forecast in this scope"
"""The line a metric table carries when its scope has no row at all."""

_MODEL_WIDTH = 12
_COUNT_WIDTH = 5
_VALUE_WIDTH = 12


def add_backtest_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add the ``backtest`` subparser to the worker command line."""
    parser = subparsers.add_parser(
        "backtest",
        help="Run the walk-forward backtest over the scored seasons.",
    )
    parser.add_argument(
        "--models",
        metavar="NAMES",
        default=MODEL_NAMES,
        type=_model_names,
        help="comma-separated model names; defaults to every registered model",
    )
    parser.add_argument(
        "--from-year",
        type=int,
        default=DEFAULT_FROM_YEAR,
        help="first scored season, given as its start year",
    )
    parser.add_argument(
        "--step-days",
        type=int,
        default=DEFAULT_STEP_DAYS,
        help="length of one walk-forward window in days",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="seed passed to every model of the run",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="output format of the report",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="print the report without storing a run row",
    )


def run_backtest_command(args: argparse.Namespace) -> int:
    """Run the backtest the parsed arguments describe and return the exit code.

    Returns:
        0 when the report was printed, 1 when the range is too small, the
        database cannot be reached or the run failed and was recorded as failed,
        2 when the database configuration is incomplete.
    """
    started = time.monotonic()
    try:
        engine = make_engine(get_settings())
    except ValueError as error:
        # The message names the missing environment variables and never a value.
        print(f"backtest: {error}", file=sys.stderr)
        return 2

    try:
        with session_factory(engine)() as session:
            return _backtest(args, session, started)
    finally:
        engine.dispose()


def scored_match_count(data: MatchData, from_year: int) -> int:
    """Return the played matches of the seasons starting in or after ``from_year``.

    A season starts in the calendar year of its earliest kickoff, exactly as the
    runner derives the season years it scores, so the count the command checks
    against :data:`MINIMUM_SCORED_MATCHES` is the number of matches the report
    will score.
    """
    start_years = _season_start_years(data)
    return sum(
        int(np.count_nonzero(data.season_id == season_id))
        for season_id, year in start_years.items()
        if year >= from_year
    )


def format_table(
    response: ModelMetricsResponse,
    *,
    data: MatchData,
    season_codes: Mapping[int, str],
) -> str:
    """Render one report as deterministic plain text.

    The text is built from the response alone except for the market coverage
    section, which needs the per-row odds source the contract does not carry: it
    is derived from ``data.odds_source`` and ``data.season_id``, restricted to
    the scored seasons. ``odds_source`` is one of the three bookmakers or
    ``None``, which is the loader's contract, so every match of a scored season
    counts exactly once.

    The expected calibration error of one model and outcome is
    ``sum(n_bin / N * abs(observed_frequency - mean_predicted))`` over the
    non-empty bins, where ``N`` is the total count of that model and outcome.
    """
    scored = _report_season_codes(response)
    blocks: list[list[str]] = [
        _header_lines(response, scored),
        _coverage_lines(data, season_codes, scored),
    ]
    for code in scored:
        blocks.append(_table_lines(f"season {code} common", _rows(response, code, "common")))
    blocks.append(_table_lines("overall all", _rows(response, None, "all")))
    blocks.append(_table_lines("overall common", _rows(response, None, "common")))
    blocks.append(_calibration_lines(response))
    blocks.append([_footnote()])
    return "\n\n".join("\n".join(block) for block in blocks)


def _model_names(value: str) -> tuple[str, ...]:
    """Split and validate a comma-separated model list.

    Raises:
        argparse.ArgumentTypeError: the list is empty, names a model that is not
            registered, or names one model twice.
    """
    names = tuple(part.strip() for part in value.split(",") if part.strip())
    known = ", ".join(MODEL_NAMES)
    if not names:
        raise argparse.ArgumentTypeError(
            f"--models needs at least one name; known models are {known}"
        )
    unknown = [name for name in names if name not in MODEL_NAMES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown model {', '.join(unknown)}; known models are {known}"
        )
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise argparse.ArgumentTypeError(
            f"model {', '.join(repeated)} given twice; known models are {known}"
        )
    return names


def _backtest(args: argparse.Namespace, session: Session, started: float) -> int:
    """Run the backtest inside one session and return the process exit code."""
    try:
        data = load_match_data(session)
    except SQLAlchemyError:
        # The driver text can carry the connection URL, so only the failure kind
        # is printed.
        print("backtest: cannot reach the database", file=sys.stderr)
        return 1
    logger.info("loaded %d played matches in %.1fs", len(data), time.monotonic() - started)

    n_matches = scored_match_count(data, args.from_year)
    if n_matches < MINIMUM_SCORED_MATCHES:
        # Before start_run: a range this small scores a number nobody can read,
        # so the command stops without writing a run row.
        print(
            f"backtest: refusing to run: {n_matches} played matches in the scored range, "
            f"at least {MINIMUM_SCORED_MATCHES} are required",
            file=sys.stderr,
        )
        return 1

    models = _requested_models(args.models, seed=args.seed)
    run_id: uuid.UUID | None = None
    if args.no_save:
        logger.info("no-save: run not stored")
    else:
        run_id = start_run(
            session,
            seed=args.seed,
            from_year=args.from_year,
            step_days=args.step_days,
            models=models,
        )
        logger.info("run %s", run_id)

    config = BacktestConfig(
        models=tuple(args.models),
        from_year=args.from_year,
        step_days=args.step_days,
        seed=args.seed,
    )
    try:
        report = run_backtest(data, config)
        season_codes = _season_codes(session)
        # The header of a run that is not stored yet: its identity stays null, and
        # the save path replaces the whole response with the stored one below.
        header = BacktestRunOut(
            id=None,
            started_at=None,
            finished_at=None,
            seed=args.seed,
            from_year=args.from_year,
            step_days=args.step_days,
            code_version=installed_code_version(),
            data_fingerprint=report.data_fingerprint,
            n_matches=n_matches,
        )
        response = report_to_response(report, run=header, models=models, season_codes=season_codes)
    except Exception as error:
        # Everything between start_run and finish_run sits under this guard: a
        # run left running by an escaping exception would never be transitioned,
        # because nothing else in the system does it.
        _mark_failed(session, run_id, error=str(error))
        print(f"backtest: {error}", file=sys.stderr)
        return 1
    except BaseException as error:
        # An interruption is not an Exception, so it would skip the guard above
        # and leave the committed run running for ever. Record it, then let the
        # original exception out unchanged: the traceback keeps its frames and is
        # the report, so nothing is returned or printed for it.
        name = type(error).__name__
        message = str(error)
        # The message of a KeyboardInterrupt is usually empty, while the class
        # name alone already says what happened.
        _mark_failed(session, run_id, error=f"{name}: {message}" if message else name)
        raise

    if run_id is not None:
        try:
            finish_run(
                session,
                run_id,
                metrics=response.metrics,
                calibration=response.calibration,
                data_fingerprint=report.data_fingerprint,
                n_matches=n_matches,
            )
            stored = load_report(session, run_id)
        except (ValueError, SQLAlchemyError) as error:
            _mark_failed(session, run_id, error=str(error))
            print(f"backtest: {error}", file=sys.stderr)
            return 1
        if stored is None:
            _mark_failed(session, run_id, error="the stored run could not be reloaded")
            print(f"backtest: run {run_id} was not stored", file=sys.stderr)
            return 1
        # The stored report is authoritative: its identity and every stored
        # digit, not the in-memory ones.
        response = stored

    for row in response.metrics:
        if row.season_code is None and row.subset == "common":
            logger.info(
                "%s: overall common log_loss=%.6f brier=%.6f rps=%.6f accuracy=%.6f",
                row.model,
                row.log_loss,
                row.brier,
                row.rps,
                row.accuracy,
            )

    rounded = round_response(response, digits=DISPLAY_DIGITS)
    if args.format == "json":
        print(rounded.model_dump_json(indent=2))
    else:
        print(format_table(rounded, data=data, season_codes=season_codes))
    logger.info("backtest: finished in %.1fs", time.monotonic() - started)
    return 0


def _requested_models(names: Sequence[str], *, seed: int) -> list[ModelInfo]:
    """Return the models of the run, in registry order, as ``describe()`` reports them."""
    models: list[ModelInfo] = []
    for name in MODEL_NAMES:
        if name not in names:
            continue
        described = make_model(name, seed=seed).describe()
        models.append(ModelInfo.model_validate({**described, "kind": MODEL_KINDS[name]}))
    return models


def _season_codes(session: Session) -> dict[int, str]:
    """Return ``seasons.id`` to ``seasons.code`` for every stored season."""
    rows = session.execute(select(Season.id, Season.code))
    return {int(season_id): str(code) for season_id, code in rows}


def _mark_failed(session: Session, run_id: uuid.UUID | None, *, error: str) -> None:
    """Record the failure of ``run_id`` on a best-effort basis.

    The caller's session may be the one whose transaction just aborted: the
    query behind a failed step runs on it, and an aborted transaction rejects
    every later statement, ``fail_run``'s own SELECT included. The rollback
    first recovers the session, so the run is still transitioned out of
    ``running``; a rollback that fails itself leaves the run to the caller.
    """
    if run_id is None:
        return
    try:
        session.rollback()
        fail_run(session, run_id, error=error)
    except ValueError, SQLAlchemyError:
        # The run may already have been finished, or the database may be gone;
        # the caller still reports the original failure.
        logger.info("could not mark run %s as failed", run_id)


def _season_start_years(data: MatchData) -> dict[int, int]:
    """Return the calendar year of the earliest kickoff of every season."""
    years: dict[int, int] = {}
    for season in np.unique(data.season_id):
        season_id = int(season)
        earliest = data.kickoff_date[data.season_id == season_id].min()
        years[season_id] = _calendar_year(earliest)
    return years


def _calendar_year(kickoff: np.datetime64) -> int:
    """Return the calendar year of one kickoff day, as the runner derives it."""
    day = kickoff.astype("datetime64[D]").item()
    if not isinstance(day, datetime.date):
        raise TypeError(f"expected a date, got {kickoff!r}")
    return day.year


def _report_season_codes(response: ModelMetricsResponse) -> list[str]:
    """Return the scored season codes in the order the report first mentions them."""
    codes: list[str] = []
    for row in response.metrics:
        if row.season_code is not None and row.season_code not in codes:
            codes.append(row.season_code)
    return codes


def _rows(
    response: ModelMetricsResponse,
    season_code: str | None,
    subset: str,
) -> list[MetricRow]:
    """Return the metric rows of one season and subset, in report order."""
    return [
        row for row in response.metrics if row.season_code == season_code and row.subset == subset
    ]


def _header_lines(response: ModelMetricsResponse, scored: Sequence[str]) -> list[str]:
    """Return the header block: the run identity and what it scored."""
    run = response.run
    seasons = ", ".join(scored) if scored else "none"
    return [
        f"seed {run.seed}",
        f"data fingerprint {run.data_fingerprint}",
        f"code version {run.code_version}",
        f"scored seasons {seasons}",
        f"scored matches {run.n_matches}",
    ]


def _coverage_lines(
    data: MatchData,
    season_codes: Mapping[int, str],
    scored: Sequence[str],
) -> list[str]:
    """Return the odds coverage of every scored season, one line per season."""
    codes_by_id = {season_id: code for season_id, code in season_codes.items() if code in scored}
    lines = ["market coverage"]
    for code in scored:
        season_ids = [season_id for season_id, other in codes_by_id.items() if other == code]
        counts = dict.fromkeys(_ODDS_LABELS, 0)
        for season_id in season_ids:
            mask = data.season_id == season_id
            for source, keep in zip(data.odds_source, mask, strict=True):
                if not keep:
                    continue
                label = "none" if source is None else source
                counts[label] = counts.get(label, 0) + 1
        totals = " ".join(f"{label}={counts[label]}" for label in _ODDS_LABELS)
        lines.append(f"season {code} {totals}")
    return lines


def _table_lines(title: str, rows: Sequence[MetricRow]) -> list[str]:
    """Return one metric table: a title, its header and one row per model.

    The runner emits no row for a scope without a scored forecast, so a table
    can be empty; :data:`_NO_DATA_LINE` says so instead of leaving a bare header
    that reads like a rendering fault.
    """
    lines = [title, _metric_header()]
    if not rows:
        lines.append(_NO_DATA_LINE)
        return lines
    lines.extend(_metric_line(row) for row in rows)
    return lines


def _metric_header() -> str:
    """Return the shared header line of every metric table."""
    columns = " ".join(f"{name:>{_VALUE_WIDTH}}" for name in _METRIC_COLUMNS)
    return f"{'model':<{_MODEL_WIDTH}} {'n':>{_COUNT_WIDTH}} {columns}"


def _metric_line(row: MetricRow) -> str:
    """Return the table line of one metric row, its floats at display precision."""
    values = (row.coverage, row.log_loss, row.brier, row.rps, row.accuracy)
    formatted = " ".join(f"{value:>{_VALUE_WIDTH}.6f}" for value in values)
    return f"{row.model:<{_MODEL_WIDTH}} {row.n_matches:>{_COUNT_WIDTH}} {formatted}"


def _calibration_lines(response: ModelMetricsResponse) -> list[str]:
    """Return the expected calibration error per model and outcome."""
    errors = _calibration_errors(response)
    lines = ["calibration expected error", _calibration_header()]
    for model in response.models:
        per_outcome = [errors.get((model.name, outcome), 0.0) for outcome in _OUTCOMES]
        values = " ".join(f"{error:>{_VALUE_WIDTH}.6f}" for error in per_outcome)
        lines.append(f"{model.name:<{_MODEL_WIDTH}} {values}")
    return lines


def _calibration_header() -> str:
    """Return the header line of the calibration summary."""
    columns = " ".join(f"{outcome:>{_VALUE_WIDTH}}" for outcome in _OUTCOMES)
    return f"{'model':<{_MODEL_WIDTH}} {columns}"


def _calibration_errors(response: ModelMetricsResponse) -> dict[tuple[str, str], float]:
    """Return the expected calibration error of every model and outcome.

    A model and outcome without a single scored forecast has no error to report
    and is left out, which the caller renders as zero.
    """
    totals: dict[tuple[str, str], int] = {}
    for row in response.calibration:
        key = (row.model, row.outcome)
        totals[key] = totals.get(key, 0) + row.n
    errors: dict[tuple[str, str], float] = {}
    for row in response.calibration:
        if row.n == 0 or row.mean_predicted is None or row.observed_frequency is None:
            continue
        key = (row.model, row.outcome)
        error = row.n / totals[key] * abs(row.observed_frequency - row.mean_predicted)
        errors[key] = errors.get(key, 0.0) + error
    return errors


def _footnote() -> str:
    """Return the one-line caveat about the constant home forecast."""
    return (
        "note: home_win log_loss is degenerate by construction: its constant forecast "
        f"is clipped at EPS={EPS:g} before scoring"
    )
