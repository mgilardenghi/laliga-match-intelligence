"""Tests for :mod:`lmi_core.config`."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from lmi_core.config import Settings, get_settings, sqlalchemy_url
from pydantic import SecretStr
from sqlalchemy.engine import make_url

ENV_VARS = (
    "DATABASE_URL",
    "REDIS_URL",
    "API_FOOTBALL_KEY",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)

POSTGRES_PARTS = {
    "POSTGRES_HOST": "db.example",
    "POSTGRES_PORT": "55432",
    "POSTGRES_USER": "lmi",
    "POSTGRES_PASSWORD": "placeholder",
    "POSTGRES_DB": "lmi",
}

TRICKY_PASSWORD = "p@ss:wo/rd?#%"


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear the settings cache and any settings env vars around each test."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set every PostgreSQL part to a placeholder value."""
    for name, value in POSTGRES_PARTS.items():
        monkeypatch.setenv(name, value)


def test_defaults_without_environment() -> None:
    settings = get_settings()

    assert settings.database_url is None
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.api_football_key is None
    assert settings.postgres_host == "localhost"
    assert settings.postgres_port == 5432
    assert settings.postgres_user is None
    assert settings.postgres_password is None
    assert settings.postgres_db is None


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


def test_sqlalchemy_url_prefers_database_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    override = "postgresql+psycopg://other:other-pw@override.example:6000/other_db"
    monkeypatch.setenv("DATABASE_URL", override)
    _set_parts(monkeypatch)

    url = sqlalchemy_url(Settings())

    assert url.render_as_string(hide_password=False) == override


def test_sqlalchemy_url_built_from_postgres_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_parts(monkeypatch)

    url = sqlalchemy_url(Settings())

    assert url.drivername == "postgresql+psycopg"
    assert url.username == "lmi"
    assert url.password == "placeholder"
    assert url.host == "db.example"
    assert url.port == 55432
    assert url.database == "lmi"


def test_sqlalchemy_url_keeps_reserved_characters_in_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_USER", "lmi")
    monkeypatch.setenv("POSTGRES_PASSWORD", TRICKY_PASSWORD)
    monkeypatch.setenv("POSTGRES_DB", "lmi")

    url = sqlalchemy_url(Settings())

    assert url.password == TRICKY_PASSWORD
    rendered = url.render_as_string(hide_password=False)
    assert make_url(rendered).password == TRICKY_PASSWORD


def test_sqlalchemy_url_names_every_missing_part(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError) as excinfo:
        sqlalchemy_url(Settings())

    message = str(excinfo.value)
    assert "POSTGRES_USER" in message
    assert "POSTGRES_PASSWORD" in message
    assert "POSTGRES_DB" in message


def test_sqlalchemy_url_error_names_only_the_missing_part_and_no_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_USER", "lmi")
    monkeypatch.setenv("POSTGRES_DB", "lmi")

    with pytest.raises(ValueError) as excinfo:
        sqlalchemy_url(Settings())

    message = str(excinfo.value)
    assert "POSTGRES_PASSWORD" in message
    assert "POSTGRES_USER" not in message
    assert "POSTGRES_DB" not in message
    assert "lmi" not in message
