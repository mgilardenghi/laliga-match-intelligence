"""Database access for the API: one session per request, one engine per process.

The engine is built on first use and never at import time. Importing this
module must succeed with no PostgreSQL reachable, because the application is
constructed when the module is imported, and a process may start before the
database accepts connections. A request that needs data is therefore the first
thing that opens a connection.

Sessions are request-scoped and read-only: M2 exposes no write endpoint, so
:func:`get_session` closes the session without ever committing.
"""

import contextlib
import functools
from collections.abc import AsyncIterator, Iterator
from typing import Annotated

from fastapi import Depends, FastAPI
from lmi_core.config import get_settings
from lmi_core.db.session import make_engine, session_factory
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


@functools.cache
def get_engine() -> Engine:
    """Return the process-wide engine, created on first use.

    The settings are read here rather than at import time, so a missing or
    incomplete database configuration fails on the first request instead of
    preventing the process from starting.

    Raises:
        ValueError: the settings do not describe a complete database URL.
    """
    return make_engine(get_settings())


@functools.cache
def get_session_factory() -> sessionmaker[Session]:
    """Return the session factory bound to the process-wide engine."""
    return session_factory(get_engine())


def get_session() -> Iterator[Session]:
    """Yield the session of one request and close it when the request ends.

    The session is closed in a ``finally`` block, so it is released whether the
    endpoint returned or raised. It is never committed: the API only reads.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]
"""The only way a route obtains a session."""


@contextlib.asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Dispose the engine on shutdown, if a request ever built one.

    Nothing happens on startup, and the cache is consulted rather than filled:
    importing this module must not need Postgres. When an engine exists it is
    disposed so its pooled connections are closed, and the engine cache is
    cleared so a second application in the same process builds a fresh engine
    instead of reusing one whose pool is already closed.

    The session factory is cached too, so it is cleared with the engine. A
    factory left in the cache stays bound to the disposed engine: the next
    request through the default dependency would reopen the pool of an engine
    nobody disposes, because the engine cache is already empty and the next
    shutdown therefore sees nothing to dispose.
    """
    yield
    if get_engine.cache_info().currsize:
        get_engine().dispose()
        get_session_factory.cache_clear()
        get_engine.cache_clear()
