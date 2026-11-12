"""Tests for :mod:`lmi_core.config`."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from lmi_core.config import Settings, get_settings
from pydantic import SecretStr

ENV_VARS = ("DATABASE_URL", "REDIS_URL", "API_FOOTBALL_KEY")


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear the settings cache and any settings env vars around each test."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults_without_environment() -> None:
    settings = get_settings()

    assert settings.database_url is None
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.api_football_key is None


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = "postgresql://user:pass@localhost:5432/example"
    redis_url = "redis://localhost:6379/1"
    api_football_key = "placeholder-key"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("API_FOOTBALL_KEY", api_football_key)

    settings = get_settings()

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == database_url
    assert settings.redis_url == redis_url
    assert settings.api_football_key is not None
    assert settings.api_football_key.get_secret_value() == api_football_key


def test_env_file_is_not_loaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A `.env` file in the working directory must not feed the settings."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://user:pass@localhost:5432/example\n")

    settings = get_settings()

    assert settings.database_url is None


def test_secrets_are_masked_in_repr_and_str(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = "postgresql://user:pass@localhost:5432/example"
    api_football_key = "placeholder-key"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("API_FOOTBALL_KEY", api_football_key)

    settings = Settings()

    assert isinstance(settings.database_url, SecretStr)
    assert isinstance(settings.api_football_key, SecretStr)
    for rendered in (repr(settings), str(settings)):
        assert database_url not in rendered
        assert api_football_key not in rendered
