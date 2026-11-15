"""The ``predict`` worker command.

M4 fills the command body that fits the model and stores the live predictions;
this stub keeps the command name, the signature and the exit-code contract.
``argv`` is accepted and ignored so the signature survives the fill.
"""

import sys
from collections.abc import Sequence

_NOT_IMPLEMENTED = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Print that the command is not implemented and return exit code 2."""
    print("predict: not implemented", file=sys.stderr)
    return _NOT_IMPLEMENTED
