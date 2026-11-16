"""The ``simulate`` subcommand: run the season simulator and print the table.

The command runs the simulator synchronously in the process that invokes it:
no queue, no Redis and no retry. The core service owns the season inputs, the
leakage rules and the stored result, so this module only translates the command
line into one run, reloads what the run wrote and renders it.

Two paths share one body. Without ``--backfill`` the command opens one live run
for the as-of date, executes it and prints the projected table. With
``--backfill`` it walks the leakage-free as-of dates of the season, skips every
date that already has a succeeded backfill run with the same size and seed, and
prints one line per date. A second identical backfill therefore adds no run.

Only the projected table and the backfill report reach stdout. Progress goes
through :data:`logger` at INFO, and a usage or database failure is one fixed
line on stderr. No line ever carries a database URL, a password or an
environment value.

The module-level collaborators are looked up at call time on purpose
(``make_engine``, ``session_factory``, ``create_run``, ``execute_run``,
``backfill_dates`` and ``madrid_today``), which is what lets the tests replace
the database without a live server.
"""

import argparse
import datetime
import logging
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from lmi_core.clock import madrid_today
from lmi_core.config import get_settings
from lmi_core.db.models import Season, SimulationRun, SimulationTeamResult, Team
from lmi_core.db.session import make_engine, session_factory
from lmi_core.simulation import DEFAULT_SEED, Qualification
from lmi_core.simulation.service import (
    MODEL_NAME,
    backfill_dates,
    create_run,
    execute_run,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from lmi_worker.seasons import current_season

logger = logging.getLogger(__name__)

DEFAULT_RUNS: Final = 10000
"""How many seasons one run simulates when ``--runs`` is absent."""

MIN_RUNS: Final = 100
"""The smallest run count the schema accepts."""

MAX_RUNS: Final = 100000
"""The largest run count the schema accepts."""

_CANNOT_REACH_DATABASE: Final = "simulate: cannot reach the database"
"""The one line a database access failure prints, with no driver detail."""

_RANK_WIDTH: Final = 4
_TEAM_WIDTH: Final = 24
_POINTS_WIDTH: Final = 11
_PROBABILITY_WIDTH: Final = 12


@dataclass(frozen=True, slots=True)
class TableRow:
    """One team's projected season, as the table prints it."""

    team_name: str
    team_id: int
    mean_points: float
    p_title: float
    p_ucl: float
    p_europe: float
    p_relegation: float


def add_simulate_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add the ``simulate`` subparser to the worker command line."""
    parser = subparsers.add_parser(
        "simulate",
        help="Run the season simulator and print the projected table.",
    )
    parser.add_argument(
        "--season",
        metavar="CODE",
        help="one season code, for example 2025-26; defaults to the current season",
    )
    parser.add_argument(
        "--as-of",
        type=_date,
        metavar="YYYY-MM-DD",
        help="last date whose played matches count as fixed; defaults to the Madrid date",
    )
    parser.add_argument(
        "--runs",
        type=_runs,
        default=DEFAULT_RUNS,
        metavar="N",
        help=f"seasons to simulate, from {MIN_RUNS} to {MAX_RUNS}",
    )
    parser.add_argument(
        "--seed",
        type=_seed,
        default=DEFAULT_SEED,
        metavar="S",
        help="seed of the model and the sampler; must be non-negative",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="run every leakage-free as-of date of the season",
    )


def run_simulate_command(args: argparse.Namespace) -> int:
    """Run the simulation the parsed arguments describe and return the exit code.

    Returns:
        0 when the live run succeeded or every attempted backfill run succeeded,
        1 when the stored run failed or the database cannot be reached, 2 for a
        configuration or usage error such as an unknown season code.
    """
    season_code = args.season if args.season is not None else _current_season_code()
    try:
        engine = make_engine(get_settings())
    except ValueError as error:
        # The message names the missing environment variables and never a value.
        print(f"simulate: {error}", file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print(_CANNOT_REACH_DATABASE, file=sys.stderr)
        return 1

    try:
        factory = session_factory(engine)
        with factory() as session:
            if args.backfill:
                return _backfill(args, session, season_code, factory)
            as_of = args.as_of if args.as_of is not None else madrid_today()
            return _live(args, session, season_code, as_of, factory)
    except SQLAlchemyError:
        # The driver text can carry the connection URL, so only the failure kind
        # is printed.
        print(_CANNOT_REACH_DATABASE, file=sys.stderr)
        return 1
    finally:
        engine.dispose()


def format_table(
    *,
    season_code: str,
    run: SimulationRun,
    rows: Sequence[TableRow],
) -> str:
    """Render one simulation as deterministic plain text.

    ``rows`` are already in projected table order, so the rank is the position
    in the sequence. The header carries the identity of the stored run and the
    counters it wrote; every probability is shown to four decimals.
    """
    lines = [
        f"season {season_code} as of {run.as_of_date.isoformat()}",
        f"runs {run.n_runs} seed {run.seed}",
        f"matches fixed {_shown(run.n_fixed)} simulated {_shown(run.n_simulated)} "
        f"withdrawn excluded {_shown(run.n_withdrawn_excluded)}",
        f"duration {_shown(run.duration_ms)} ms",
        f"data fingerprint {run.data_fingerprint or 'unknown'}",
        "",
        _table_header(),
    ]
    lines.extend(_table_line(rank, row) for rank, row in enumerate(rows, start=1))
    return "\n".join(lines)


def _current_season_code() -> str:
    """Return the season in progress on the current Madrid date."""
    return current_season(madrid_today()).code


def _date(value: str) -> datetime.date:
    """Parse one ``YYYY-MM-DD`` date for ``--as-of``.

    Raises:
        argparse.ArgumentTypeError: the text is not exactly ``YYYY-MM-DD``.
    """
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}: expected YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}: expected YYYY-MM-DD")
    return parsed


def _runs(value: str) -> int:
    """Parse and range-check the ``--runs`` value.

    Raises:
        argparse.ArgumentTypeError: the value is not an integer in
            ``[MIN_RUNS, MAX_RUNS]``.
    """
    try:
        runs = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid run count {value!r}: expected an integer"
        ) from None
    if not MIN_RUNS <= runs <= MAX_RUNS:
        raise argparse.ArgumentTypeError(
            f"invalid run count {runs}: expected {MIN_RUNS} to {MAX_RUNS}"
        )
    return runs


def _seed(value: str) -> int:
    """Parse and check the non-negative ``--seed`` value.

    Raises:
        argparse.ArgumentTypeError: the value is not a non-negative integer.
    """
    try:
        seed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid seed {value!r}: expected an integer") from None
    if seed < 0:
        raise argparse.ArgumentTypeError(f"invalid seed {seed}: must be non-negative")
    return seed


def _live(
    args: argparse.Namespace,
    session: Session,
    season_code: str,
    as_of: datetime.date,
    factory: sessionmaker[Session],
) -> int:
    """Run one live simulation, print its table and return the exit code."""
    try:
        run, _ = create_run(
            session,
            season_code=season_code,
            as_of_date=as_of,
            n_runs=args.runs,
            seed=args.seed,
            trigger="cli",
            qualification=Qualification(),
            kind="live",
        )
    except ValueError as error:
        print(f"simulate: {error}", file=sys.stderr)
        return 2

    logger.info("live run %s for %s as of %s", run.id, season_code, as_of)
    execute_run(factory, run.id)
    stored = _reload(session, run.id)
    if stored is None:
        print(f"simulate: run {run.id} disappeared before it could be read", file=sys.stderr)
        return 1
    if stored.status != "succeeded":
        print(_failure_line(stored), file=sys.stderr)
        return 1

    rows = _team_rows(session, run.id)
    print(format_table(season_code=season_code, run=stored, rows=rows))
    logger.info("simulate: run %s finished with %d team rows", run.id, len(rows))
    return 0


def _backfill(
    args: argparse.Namespace,
    session: Session,
    season_code: str,
    factory: sessionmaker[Session],
) -> int:
    """Run every missing backfill date, print one line per date and return the code."""
    try:
        dates = backfill_dates(session, season_code)
    except ValueError as error:
        print(f"simulate: {error}", file=sys.stderr)
        return 2

    succeeded = _succeeded_backfills(session, season_code, n_runs=args.runs, seed=args.seed)
    all_succeeded = True
    for day in dates:
        prior_run_id = succeeded.get(day)
        if prior_run_id is not None:
            # A second identical invocation finds every date here and writes
            # nothing, which is the backfill's idempotence.
            print(f"{day.isoformat()} {prior_run_id} skipped")
            continue

        try:
            run, _ = create_run(
                session,
                season_code=season_code,
                as_of_date=day,
                n_runs=args.runs,
                seed=args.seed,
                trigger="cli",
                qualification=Qualification(),
                kind="backfill",
            )
        except ValueError as error:
            print(f"simulate: {error}", file=sys.stderr)
            return 2

        if run.kind != "backfill":
            # The natural key of an active run does not include its kind, so an
            # active live run requested for this date holds it. Executing it
            # here would print a live run as a backfilled date, and the next
            # invocation would still find no backfill run for that date and try
            # again; the date is therefore reported and left alone.
            print(f"{day.isoformat()} {run.id} blocked by {run.kind} run")
            print(
                f"simulate: {day.isoformat()} is held by the active {run.kind} run {run.id}",
                file=sys.stderr,
            )
            all_succeeded = False
            continue

        logger.info("backfill run %s for %s as of %s", run.id, season_code, day)
        execute_run(factory, run.id)
        stored = _reload(session, run.id)
        if stored is None:
            print(f"{day.isoformat()} {run.id} missing")
            all_succeeded = False
            continue
        print(f"{day.isoformat()} {run.id} {stored.status}")
        if stored.status != "succeeded":
            all_succeeded = False
            print(_failure_line(stored), file=sys.stderr)

    logger.info("simulate: backfill finished, every attempted run succeeded: %s", all_succeeded)
    return 0 if all_succeeded else 1


def _succeeded_backfills(
    session: Session,
    season_code: str,
    *,
    n_runs: int,
    seed: int,
) -> dict[datetime.date, uuid.UUID]:
    """Return the run id of every succeeded backfill keyed by its as-of date.

    The key is the run's own as-of date: a backfill date is not the date the
    command runs, it is the simulated cut of that run.
    """
    statement = (
        select(SimulationRun)
        .join(Season, Season.id == SimulationRun.season_id)
        .where(
            Season.code == season_code,
            SimulationRun.n_runs == n_runs,
            SimulationRun.seed == seed,
            SimulationRun.model == MODEL_NAME,
            SimulationRun.kind == "backfill",
            SimulationRun.status == "succeeded",
        )
    )
    return {run.as_of_date: run.id for run in session.scalars(statement)}


def _reload(session: Session, run_id: uuid.UUID) -> SimulationRun | None:
    """Expire the session's copies and read the run the executor committed."""
    session.expire_all()
    return session.get(SimulationRun, run_id)


def _team_rows(session: Session, run_id: uuid.UUID) -> list[TableRow]:
    """Return the stored team rows in projected table order."""
    statement = (
        select(SimulationTeamResult, Team.name)
        .join(Team, Team.id == SimulationTeamResult.team_id)
        .where(SimulationTeamResult.run_id == run_id)
        .order_by(
            SimulationTeamResult.mean_position,
            SimulationTeamResult.mean_points.desc(),
            Team.name,
            Team.id,
        )
    )
    return [
        TableRow(
            team_name=str(name),
            team_id=int(result.team_id),
            mean_points=float(result.mean_points),
            p_title=float(result.p_title),
            p_ucl=float(result.p_ucl),
            p_europe=float(result.p_europe),
            p_relegation=float(result.p_relegation),
        )
        for result, name in session.execute(statement)
    ]


def _failure_line(run: SimulationRun) -> str:
    """Return the one stderr line that names why the run did not succeed."""
    if run.status == "failed":
        return f"simulate: run {run.id} failed: {run.error or 'no error recorded'}"
    return f"simulate: run {run.id} did not finish: status {run.status}"


def _table_header() -> str:
    """Return the header line of the projected table."""
    return (
        f"{'rank':>{_RANK_WIDTH}} {'team':<{_TEAM_WIDTH}} "
        f"{'mean_points':>{_POINTS_WIDTH}} {'p_title':>{_PROBABILITY_WIDTH}} "
        f"{'p_ucl':>{_PROBABILITY_WIDTH}} {'p_europe':>{_PROBABILITY_WIDTH}} "
        f"{'p_relegation':>{_PROBABILITY_WIDTH}}"
    )


def _table_line(rank: int, row: TableRow) -> str:
    """Return one table line, its name aligned and its probabilities at four decimals."""
    return (
        f"{rank:>{_RANK_WIDTH}} {row.team_name:<{_TEAM_WIDTH}} "
        f"{row.mean_points:>{_POINTS_WIDTH}.1f} {row.p_title:>{_PROBABILITY_WIDTH}.4f} "
        f"{row.p_ucl:>{_PROBABILITY_WIDTH}.4f} {row.p_europe:>{_PROBABILITY_WIDTH}.4f} "
        f"{row.p_relegation:>{_PROBABILITY_WIDTH}.4f}"
    )


def _shown(value: int | None) -> str:
    """Return a stored counter, or ``unknown`` when the run did not write it."""
    return "unknown" if value is None else str(value)
