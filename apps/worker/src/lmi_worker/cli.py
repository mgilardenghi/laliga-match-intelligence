"""Command line entry point for the LMI worker."""

import argparse
import logging
from collections.abc import Sequence

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the worker CLI."""
    parser = argparse.ArgumentParser(
        prog="lmi_worker",
        description="Ingestion, model fitting and simulation jobs for LMI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ingest", help="Run the ingestion job.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the arguments and run the requested job."""
    args = build_parser().parse_args(argv)
    if args.command == "ingest":
        logger.info("ingest: not implemented yet")
        return 0
    raise ValueError(f"unhandled command: {args.command}")
