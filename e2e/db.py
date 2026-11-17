"""Database access for the browser tests.

The loaded tier needs a state the loader cannot produce: a played fixture that
still carries the forecast the worker stored before kickoff, because ``predict``
writes a row only while the match is scheduled. A test therefore updates the
match row itself, and this module is the one place that reaches the throwaway
database to do it.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
"""The repository root, where the Compose files live."""

THROWAWAY_PROJECT = re.compile(r"lmi-e2e(-[a-z0-9][a-z0-9_-]*)?")
"""The Compose project names a browser test may write to."""


def psql(sql: str) -> str:
    """Run ``sql`` in the throwaway e2e database and return its unaligned output.

    The project name comes from the same ``E2E_PROJECT`` variable the Makefile
    guard validates, and it is passed as an argument, never through a shell.
    """
    project = os.environ.get("E2E_PROJECT", "lmi-e2e")
    # This helper writes, and a test can be run without `make`, so the Makefile
    # guard is not enough. An empty project name makes Compose fall back to the
    # `name: lmi` of docker-compose.yml, which is the developer's own stack. Only
    # the throwaway family (`lmi-e2e`, `lmi-e2e-u2`, ...) is accepted.
    if THROWAWAY_PROJECT.fullmatch(project) is None:
        pytest.fail(
            f"E2E_PROJECT {project!r} is not a throwaway project name "
            "(lmi-e2e or lmi-e2e-<suffix>); refusing to write to it"
        )
    command = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        "docker-compose.yml",
        "-f",
        "e2e/compose.e2e.yml",
        "exec",
        "-T",
        "db",
        "sh",
        "-c",
        'psql -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
    ]
    result = subprocess.run(
        command, input=sql, capture_output=True, text=True, check=True, cwd=REPO_ROOT
    )
    return result.stdout
