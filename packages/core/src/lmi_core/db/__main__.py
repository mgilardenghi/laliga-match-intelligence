"""Command line entry point of the database job.

``python -m lmi_core.db migrate`` upgrades the schema to the head revision and
then inserts the reference data. It is the command behind the one-shot
``migrate`` Compose service, so its exit code gates the rest of the stack: any
exception propagates and the process exits non-zero.

Output is one short line per phase. Neither the database URL nor a password is
ever printed, and building the parser needs no database, so ``--help`` works
without configuration.
"""

import argparse
from collections.abc import Callable, Sequence

from lmi_core.config import get_settings
from lmi_core.db.alembic_support import upgrade_to_head
from lmi_core.db.seed import seed
from lmi_core.db.session import make_engine


def migrate() -> None:
    """Apply the migrations, then insert the missing reference data."""
    settings = get_settings()
    print("schema: applying migrations")
    upgrade_to_head(settings)
    print("schema: at head")
    print("seed: inserting reference data")
    seed(make_engine(settings))
    print("seed: done")


def build_parser() -> argparse.ArgumentParser:
    """Return the parser of ``python -m lmi_core.db``."""
    parser = argparse.ArgumentParser(
        prog="python -m lmi_core.db",
        description="Database maintenance commands for the LMI stack.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    migrate_command = commands.add_parser(
        "migrate",
        help="apply the migrations, then insert missing reference data",
        description="Apply the migrations to head, then seed seasons, teams and aliases.",
    )
    migrate_command.set_defaults(handler=migrate)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run the command named on the command line."""
    arguments = build_parser().parse_args(argv)
    handler: Callable[[], None] = arguments.handler
    handler()


if __name__ == "__main__":
    main()
