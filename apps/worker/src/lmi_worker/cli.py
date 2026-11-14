"""Command line entry point for the LMI worker.

Two commands exist in M1. ``ingest`` downloads one source file per planned job,
loads it and records every run; ``status`` prints the current state of the data
without touching the network. ``python -m lmi_worker`` runs the same entry point,
and the Compose worker uses ``status`` so starting the stack never contacts a
source.

The plan is built before the settings and the engine on purpose: a usage error,
an out-of-range season or an impossible source selection therefore exits with
code 2 without opening a database connection and without writing anything.
"""

import argparse
import functools
import logging
import sys
from collections.abc import Sequence
from datetime import date

import httpx
from lmi_core.config import get_settings
from lmi_core.db.session import make_engine
from sqlalchemy.exc import SQLAlchemyError

from lmi_worker import http
from lmi_worker.ingest.pipeline import Fetcher, JobResult, run_jobs
from lmi_worker.ingest.plan import PlanError, plan_jobs
from lmi_worker.status import collect_status, format_status

logger = logging.getLogger(__name__)

_SOURCE_CHOICES = ("football_data", "openfootball", "all")
"""The values the ``--source`` flag accepts."""

_PAUSE_SECONDS = 1.0
"""The wait between two downloads, so the sources are not hammered."""


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the worker CLI."""
    parser = argparse.ArgumentParser(
        prog="lmi_worker",
        description="Ingestion, model fitting and simulation jobs for LMI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser(
        "ingest",
        help="Download and load one season, the current season or a backfill.",
    )
    selection = ingest.add_mutually_exclusive_group()
    selection.add_argument(
        "--season",
        metavar="CODE",
        help="one season code, for example 2025-26; wins over the other flags",
    )
    selection.add_argument(
        "--backfill",
        action="store_true",
        help="every season from 2005-06 through the current one",
    )
    selection.add_argument(
        "--backfill-through",
        metavar="CODE",
        dest="backfill_through",
        help="every season from 2005-06 through this code",
    )
    ingest.add_argument(
        "--source",
        choices=_SOURCE_CHOICES,
        default="all",
        help="which source to load; openfootball covers only the current season",
    )

    subparsers.add_parser(
        "status",
        help="Print the stored seasons, table counts, latest runs and digest.",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    today: date | None = None,
    fetcher: Fetcher | None = None,
) -> int:
    """Parse the arguments and run the requested command.

    Returns:
        0 when the command succeeds, 1 when a job failed or was locked or the
        status cannot reach the database, 2 for a usage, plan or configuration
        error. ``argparse`` exits with 2 itself for a malformed command line.
    """
    args = build_parser().parse_args(argv)
    if args.command == "ingest":
        return _ingest(args, today=today, fetcher=fetcher)
    if args.command == "status":
        return _status()
    raise ValueError(f"unhandled command: {args.command}")


def _ingest(
    args: argparse.Namespace,
    *,
    today: date | None,
    fetcher: Fetcher | None,
) -> int:
    """Build the plan, run every job and return the process exit code."""
    day = today or date.today()
    try:
        jobs = plan_jobs(
            season=args.season,
            backfill=args.backfill,
            backfill_through=args.backfill_through,
            sources=args.source,
            today=day,
        )
    except PlanError as error:
        print(f"ingest: {error}", file=sys.stderr)
        return 2

    try:
        engine = make_engine(get_settings())
    except ValueError as error:
        # The message names the missing environment variables and never a value.
        print(f"ingest: {error}", file=sys.stderr)
        return 2

    client: httpx.Client | None = None
    try:
        if fetcher is None:
            client = httpx.Client()
            download: Fetcher = functools.partial(http.fetch, client=client)
        else:
            download = fetcher
        results = run_jobs(
            engine,
            jobs,
            download,
            functools.partial(http.polite_pause, _PAUSE_SECONDS),
        )
    finally:
        if client is not None:
            client.close()
        engine.dispose()

    for result in results:
        _log_result(result)
    succeeded = sum(1 for result in results if result.status == "succeeded")
    failed = len(results) - succeeded
    logger.info("ingest: %d succeeded, %d failed", succeeded, failed)
    return 0 if failed == 0 else 1


def _status() -> int:
    """Print the status report and return the process exit code."""
    try:
        engine = make_engine(get_settings())
    except ValueError as error:
        print(f"status: {error}", file=sys.stderr)
        return 2

    try:
        report = collect_status(engine)
    except SQLAlchemyError:
        # The exception text can carry the connection URL, so only the failure
        # kind is printed.
        print("status: cannot reach the database", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    print(format_status(report))
    return 0


def _log_result(result: JobResult) -> None:
    """Log the one-line outcome of one job."""
    run_id = "none" if result.run_id is None else str(result.run_id)
    logger.info(
        "%s %s %s read=%d upserted=%d removed=%d rejected=%d run=%s",
        result.job.source,
        result.job.season.code,
        result.status,
        result.rows_read,
        result.rows_upserted,
        result.rows_removed,
        result.rows_rejected,
        run_id,
    )
