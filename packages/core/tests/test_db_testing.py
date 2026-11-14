"""Tests for :mod:`lmi_core.db.testing`."""

import os

import pytest
from lmi_core.db.testing import require_disposable_database

_DATABASE_URL = (
    "postgresql://sentinel_user:sentinel_password@sentinel-host.invalid:5432/sentinel_db"
)
"""A URL whose every component is distinguishable, so a leak is detectable."""

_DATABASE_URL_PARTS = (
    "postgresql://",
    "sentinel_user",
    "sentinel_password",
    "sentinel-host.invalid",
    "sentinel_db",
)
"""The parts of :data:`_DATABASE_URL` that must never reach a refusal message."""


def clear_variable(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Remove every spelling of ``name``, because the check ignores letter case."""
    for present in [key for key in os.environ if key.lower() == name.lower()]:
        monkeypatch.delenv(present)


def assert_no_url_part(message: str) -> None:
    """Fail when ``message`` carries any component of :data:`_DATABASE_URL`."""
    for part in _DATABASE_URL_PARTS:
        assert part not in message, f"the refusal message leaks {part!r}"


def assert_url_refusal(message: str) -> None:
    """Fail unless ``message`` is the refusal caused by ``DATABASE_URL``.

    The opt-in branch mentions :data:`ALLOW_DESTRUCTIVE_TESTS_VARIABLE` instead, so
    its absence proves the URL branch ran rather than the opt-in branch.
    """
    assert "DATABASE_URL" in message
    assert "LMI_ALLOW_DESTRUCTIVE_TESTS" not in message
    assert_no_url_part(message)


def test_require_disposable_database_refuses_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_variable(monkeypatch, "LMI_ALLOW_DESTRUCTIVE_TESTS")
    clear_variable(monkeypatch, "DATABASE_URL")

    with pytest.raises(RuntimeError) as refused:
        require_disposable_database()

    assert "LMI_ALLOW_DESTRUCTIVE_TESTS" in str(refused.value)


def test_require_disposable_database_refuses_a_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LMI_ALLOW_DESTRUCTIVE_TESTS", "1")
    clear_variable(monkeypatch, "DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", _DATABASE_URL)

    with pytest.raises(RuntimeError) as refused:
        require_disposable_database()

    assert_url_refusal(str(refused.value))


def test_require_disposable_database_refuses_every_spelling_of_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pydantic settings match environment names in any letter case."""
    monkeypatch.setenv("LMI_ALLOW_DESTRUCTIVE_TESTS", "1")
    clear_variable(monkeypatch, "DATABASE_URL")
    for spelling in ("database_url", "Database_Url"):
        monkeypatch.setenv(spelling, _DATABASE_URL)
        with pytest.raises(RuntimeError) as refused:
            require_disposable_database()
        assert_url_refusal(str(refused.value))
        monkeypatch.delenv(spelling)


def test_require_disposable_database_refuses_an_empty_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty value is still a set variable, and the contract says present is refused."""
    monkeypatch.setenv("LMI_ALLOW_DESTRUCTIVE_TESTS", "1")
    clear_variable(monkeypatch, "DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", "")

    with pytest.raises(RuntimeError) as refused:
        require_disposable_database()

    assert_url_refusal(str(refused.value))


def test_require_disposable_database_accepts_a_throwaway_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit opt-in with no ``DATABASE_URL`` is accepted and returns nothing."""
    monkeypatch.setenv("LMI_ALLOW_DESTRUCTIVE_TESTS", "1")
    clear_variable(monkeypatch, "DATABASE_URL")

    require_disposable_database()

    assert not [key for key in os.environ if key.lower() == "database_url"]
