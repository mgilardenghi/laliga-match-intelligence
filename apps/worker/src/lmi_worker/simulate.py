"""The ``simulate`` worker command.

M4 fills the command body that runs the season simulator and stores the
simulation results; this stub keeps the command name, the signature and the
exit-code contract. ``argv`` is accepted and ignored so the signature survives
the fill.
"""

import sys
from collections.abc import Sequence

_NOT_IMPLEMENTED = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Print that the command is not implemented and return exit code 2."""
    print("simulate: not implemented", file=sys.stderr)
    return _NOT_IMPLEMENTED
