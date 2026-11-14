"""Tests for :mod:`lmi_core.db.testing`."""

import pytest
from lmi_core.db.testing import require_disposable_database


def test_require_disposable_database_refuses_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LMI_ALLOW_DESTRUCTIVE_TESTS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError) as refused:
        require_disposable_database()

    assert "LMI_ALLOW_DESTRUCTIVE_TESTS" in str(refused.value)


def test_require_disposable_database_refuses_a_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = "postgresql://user:placeholder@db.example:5432/example"
    monkeypatch.setenv("LMI_ALLOW_DESTRUCTIVE_TESTS", "1")
    monkeypatch.setenv("DATABASE_URL", database_url)

    with pytest.raises(RuntimeError) as refused:
        require_disposable_database()

    message = str(refused.value)
    assert "DATABASE_URL" in message
    assert database_url not in message


def test_require_disposable_database_accepts_a_throwaway_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit opt-in with no ``DATABASE_URL`` is accepted and returns nothing."""
    monkeypatch.setenv("LMI_ALLOW_DESTRUCTIVE_TESTS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    require_disposable_database()
