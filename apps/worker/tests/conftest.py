"""Shared fixtures for the worker test suite.

Two guarantees hold for every worker test.

The first is that no test can reach the network. A single autouse fixture replaces
``httpx.HTTPTransport.handle_request``, the only place where ``httpx`` opens a
socket, with a function that raises :class:`RuntimeError`. ``httpx.MockTransport``
overrides that method itself, so a test that wants a transport substitutes its own
handler and is unaffected.

The second is that the tests needing a live PostgreSQL state their requirements
instead of degrading. ``migrated_engine`` first calls
:func:`lmi_core.db.testing.require_disposable_database`, because it drops the
``public`` schema it connects to, and then fails when one of the ``POSTGRES_*``
variables is unset. It never skips: an integration run without a database is a
configuration error, and the message names the variable to set. The fixture then
migrates to head and seeds the reference data, so a test starts from the same
schema and the same 22 seasons, 41 teams and alias rows as a deployed database.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
from lmi_core.config import Settings
from lmi_core.db.alembic_support import upgrade_to_head
from lmi_core.db.seed import seed
from lmi_core.db.session import make_engine, session_factory
from lmi_core.db.testing import require_disposable_database
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

POSTGRES_ENVIRONMENT_VARIABLES = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)
"""The variables the migrated database requires; a missing one is a hard failure."""


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse every real HTTP request for the whole worker suite."""

    def refuse_request(transport: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        raise RuntimeError("live network disabled in tests")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse_request)


@dataclass(frozen=True)
class MigratedDatabase:
    """A migrated, seeded, disposable database and the tools to query it."""

    engine: Engine
    settings: Settings
    factories: sessionmaker[Session]


@pytest.fixture
def migrated_engine() -> Iterator[MigratedDatabase]:
    """Yield a migrated and seeded database whose ``public`` schema was dropped.

    Every test starts from ``DROP SCHEMA public CASCADE`` plus ``CREATE SCHEMA
    public``, so the tests do not depend on each other or on their order.

    Raises:
        pytest.fail: the destructive-test guard refused the environment, or one of
            the ``POSTGRES_*`` variables is missing. The test is not skipped: an
            integration run without a database is a configuration error, and the
            message names the variable to set.
    """
    try:
        require_disposable_database()
    except RuntimeError as refusal:
        pytest.fail(str(refusal), pytrace=False)

    missing = [name for name in POSTGRES_ENVIRONMENT_VARIABLES if not os.environ.get(name)]
    if missing:
        pytest.fail(
            "the integration tests need a live PostgreSQL: "
            + ", ".join(missing)
            + " is not set; run `make test-integration`",
            pytrace=False,
        )

    settings = Settings()
    engine = make_engine(settings)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        upgrade_to_head(settings)
        seed(engine)
        yield MigratedDatabase(engine=engine, settings=settings, factories=session_factory(engine))
    finally:
        engine.dispose()
