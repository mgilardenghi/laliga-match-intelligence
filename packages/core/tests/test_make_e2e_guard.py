"""The guard in front of every throwaway browser-test target.

``make e2e-down`` runs ``down -v`` in the Compose project named by ``E2E_PROJECT``,
so a value that reached the developer's own ``lmi`` project would delete their
data. ``e2e-guard`` runs before any Docker command and calls none itself, so these
tests drive the real target and need no Docker.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def run_guard(**overrides: str) -> subprocess.CompletedProcess[str]:
    """Run ``make e2e-guard`` with ``E2E_*`` overrides set in the environment."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("E2E_")}
    env.update(overrides)
    return subprocess.run(
        ["make", "--no-print-directory", "e2e-guard"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_the_defaults_pass_silently() -> None:
    result = run_guard()

    assert result.returncode == 0
    assert result.stderr == ""


def test_a_plain_name_and_free_ports_pass() -> None:
    result = run_guard(E2E_PROJECT="lmi-e2e-test", E2E_API_PORT="18500", E2E_WEB_PORT="13500")

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "project",
    [
        pytest.param("", id="empty"),
        pytest.param("lmi", id="the-local-stack"),
        pytest.param("LMI", id="upper-case"),
        pytest.param("lmi ", id="trailing-space"),
        pytest.param(" lmi", id="leading-space"),
        pytest.param("a b", id="inner-space"),
        pytest.param("_lmi-x", id="leading-underscore"),
        pytest.param("x; echo injected #", id="shell-metacharacters"),
    ],
)
def test_a_project_name_that_could_reach_the_local_stack_is_rejected(project: str) -> None:
    result = run_guard(E2E_PROJECT=project)

    assert result.returncode != 0
    assert "e2e-guard: E2E_PROJECT" in result.stderr


@pytest.mark.parametrize(
    ("api", "web", "reason"),
    [
        pytest.param("8000", "13500", "reserved", id="local-api-port"),
        pytest.param("18500", "3000", "reserved", id="local-web-port"),
        pytest.param("55463", "13500", "reserved", id="a-test-database-port"),
        pytest.param("1", "13500", "between 1024 and 65535", id="below-the-range"),
        pytest.param("65536", "13500", "between 1024 and 65535", id="above-the-range"),
        pytest.param("1e5", "13500", "numeric", id="not-a-number"),
        pytest.param("18500", "18500", "must differ", id="equal-ports"),
    ],
)
def test_a_port_that_collides_or_is_malformed_is_rejected(api: str, web: str, reason: str) -> None:
    result = run_guard(E2E_API_PORT=api, E2E_WEB_PORT=web)

    assert result.returncode != 0
    assert reason in result.stderr


def test_the_injection_probe_creates_no_file(tmp_path: Path) -> None:
    marker = tmp_path / "injected"

    result = run_guard(E2E_PROJECT=f"x; touch {marker} #")

    assert result.returncode != 0
    assert not marker.exists()
