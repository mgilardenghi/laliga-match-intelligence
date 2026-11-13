"""Behavior tests for the worker command line entry point."""

import logging
import subprocess
import sys

import pytest
from lmi_worker.cli import main


def test_ingest_returns_zero_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        assert main(["ingest"]) == 0

    assert "ingest: not implemented yet" in caplog.messages


def test_ingest_logs_when_run_as_a_process() -> None:
    """The log record must reach stderr through the real process entry point."""
    result = subprocess.run(
        [sys.executable, "-m", "lmi_worker", "ingest"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "ingest: not implemented yet" in result.stderr


def test_help_exits_with_zero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0
