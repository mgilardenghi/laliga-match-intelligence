"""Database access for the browser tests.

The loaded tier needs a state the loader cannot produce: a played fixture that
still carries the forecast the worker stored before kickoff, because ``predict``
writes a row only while the match is scheduled. A test therefore updates the
match row itself, and this module is the one place that reaches the throwaway
database to do it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
"""The repository root, where the Compose files live."""


def psql(sql: str) -> str:
    """Run ``sql`` in the throwaway e2e database and return its unaligned output.

    The project name comes from the same ``E2E_PROJECT`` variable the Makefile
    guard validates, and it is passed as an argument, never through a shell.
    """
    project = os.environ.get("E2E_PROJECT", "lmi-e2e")
    # The developer's own stack is the Compose project `lmi`; this helper writes,
    # so it refuses that name even though the Makefile guard already does.
    if project.lower() == "lmi":
        pytest.fail("E2E_PROJECT names the developer stack `lmi`; refusing to write to it")
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
