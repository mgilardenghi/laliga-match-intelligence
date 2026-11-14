"""Behavior tests for the worker command line entry point without a database.

The tests cover the two exit-code paths that must work before any connection
exists: a successful ``--help``, a malformed command line, and a missing database
configuration for both commands. Every command is run with the ``POSTGRES_*``
variables removed, so the configuration error is the one a fresh shell sees.

``get_settings`` is cached, so the tests clear the cache around the environment
change; the subprocess test starts from a clean process and does not need it.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import date

import pytest
from ingest_support import FakeFetcher
from lmi_core.config import get_settings
from lmi_worker.cli import main

TODAY = date(2026, 11, 14)
"""A date inside the 2026-27 season, the newest seeded season."""

_CONFIGURATION_VARIABLES = (
    "DATABASE_URL",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove every database setting for the duration of one test."""
    for name in _CONFIGURATION_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_help_exits_with_zero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0


def test_two_exclusive_season_flags_exit_with_two() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["ingest", "--season", "2026-27", "--backfill"])

    assert excinfo.value.code == 2


def test_ingest_without_configuration_exits_two_and_fetches_nothing(
    unconfigured: None, capsys: pytest.CaptureFixture[str]
) -> None:
    fetcher = FakeFetcher({})

    code = main(["ingest", "--season", "2026-27"], today=TODAY, fetcher=fetcher)

    assert code == 2
    assert "incomplete database configuration" in capsys.readouterr().err
    assert fetcher.requested == []


def test_status_without_configuration_exits_two_in_a_subprocess() -> None:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("POSTGRES_") and name.upper() != "DATABASE_URL"
    }

    result = subprocess.run(
        [sys.executable, "-m", "lmi_worker", "status"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 2
    assert "incomplete database configuration" in result.stderr
