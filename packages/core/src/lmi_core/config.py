"""Runtime configuration shared by the LMI services.

Every value is read from an environment variable. There is deliberately no
env-file support: the deployment environment provides the values, and no
credential has a default in this repository.

Values that can carry credentials are wrapped in :class:`pydantic.SecretStr` so
they do not appear in logs or tracebacks. ``redis_url`` is a plain string, so it
must stay credential-free: a password inside it would be rendered in ``repr``.
A deployment that needs Redis authentication has to revisit this contract first.
"""

import functools

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration for one LMI process, built from environment variables."""

    database_url: SecretStr | None = None
    redis_url: str = "redis://localhost:6379/0"
    api_football_key: SecretStr | None = None


@functools.lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings object, built on first use."""
    return Settings()
