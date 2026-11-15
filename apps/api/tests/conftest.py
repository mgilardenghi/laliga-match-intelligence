"""Shared fixtures for the API test suite.

Two guarantees hold for every API test.

The first is that neither importing this module nor collecting the suite touches
a database. Nothing here calls ``Settings()``, ``make_engine`` or
``upgrade_to_head`` at import time: those calls live inside the session-scoped
``api_engine`` fixture, which only runs when a test asks for it. ``make test``
therefore runs the whole unit suite with no PostgreSQL reachable, and a
collection error there means a fixture connected too early.

The second is that a test needing a live PostgreSQL states its requirements
instead of degrading. ``api_engine`` first calls
:func:`lmi_core.db.testing.require_disposable_database` and fails with the
refusal when the environment is not explicitly disposable, then fails naming
the unset ``POSTGRES_*`` variable. It never skips: an integration run without a
database is a configuration error, and a silent skip would report a green suite
that proved nothing.

The database-backed fixtures keep every test inside one transaction that is
always rolled back, so a test sees only its own rows even though the suite
shares one database with the seeded seasons and the ingested matches.
"""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from lmi_api.db import get_session
from lmi_api.main import create_app
from lmi_core.config import Settings
from lmi_core.db.alembic_support import upgrade_to_head
from lmi_core.db.session import make_engine
from lmi_core.db.testing import require_disposable_database
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

POSTGRES_ENVIRONMENT_VARIABLES = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)
"""The variables the database-backed fixtures require; a missing one is a hard failure."""


@pytest.fixture(scope="session")
def api_engine() -> Iterator[Engine]:
    """Yield an engine for the disposable database, migrated to head.

    The migration runs here, unconditionally, on every run. It is idempotent, so
    repeating it costs nothing, and it is what lets these tests run first
    against a freshly created server, the CI case: the throwaway container is
    empty, and the schema the routes select from has to exist before the first
    request. The migration is not owned by this fixture; the fixture only
    guarantees the schema is at head.

    This fixture adds no destructive guard of its own, and it must not need one:
    it never drops a schema, truncates a table or rewrites a stored row, so the
    destructive-test check belongs to the fixture that does. The call to
    :func:`~lmi_core.db.testing.require_disposable_database` is still made,
    because the suite shares one database and a run pointed at a real one must
    fail here, before the transaction fixture connects to it.

    Raises:
        pytest.fail: the destructive-test guard refused the environment, or one
            of the ``POSTGRES_*`` variables is unset. The test is not skipped:
            the message names the variable to set.
    """
    try:
        require_disposable_database()
    except RuntimeError as refusal:
        pytest.fail(str(refusal), pytrace=False)

    missing = [name for name in POSTGRES_ENVIRONMENT_VARIABLES if not os.environ.get(name)]
    if missing:
        pytest.fail(
            "the API integration tests need a live PostgreSQL: "
            + ", ".join(missing)
            + " is not set; run `make test-integration`",
            pytrace=False,
        )

    settings = Settings()
    upgrade_to_head(settings)
    engine = make_engine(settings)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(api_engine: Engine) -> Iterator[Session]:
    """Yield a session whose every write is rolled back when the test ends.

    One outer transaction is opened on a dedicated connection and the session
    joins it, so each ``flush()`` of a factory becomes a savepoint inside that
    transaction and no row is ever committed. The outer transaction is rolled
    back in ``finally`` whatever the test did, so a test never sees another
    test's rows, the order of the tests does not matter, and two consecutive
    runs of the suite start and end in the same database state.
    """
    connection: Connection = api_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def api_client(db_session: Session) -> Iterator[TestClient]:
    """Yield a client of the application that reads through ``db_session``.

    The dependency override replaces the request-scoped session of the
    application with the one this test owns, so every request made through the
    client reads inside the test's outer transaction: rows a factory flushed are
    visible to the endpoint, and nothing the endpoint does can outlive the test.

    The client is entered as a context manager so the application lifespan runs
    before the first request, as it does in a deployed process. The overrides are
    cleared afterwards, so an application instance never keeps a session that
    belongs to a finished test.
    """
    application = create_app()
    application.dependency_overrides[get_session] = lambda: db_session
    try:
        with TestClient(application) as client:
            yield client
    finally:
        application.dependency_overrides.clear()
