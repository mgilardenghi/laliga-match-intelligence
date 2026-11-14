"""Engine and session construction for the LMI database.

Engines are created by the process that uses them, never at import time. The
API and the worker each build one engine from the shared settings and derive
their session factories from it. :func:`lmi_core.config.sqlalchemy_url` owns
how the connection URL is assembled, including the ``DATABASE_URL`` override.
"""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from lmi_core.config import Settings, sqlalchemy_url


def make_engine(settings: Settings) -> Engine:
    """Return a new engine for the application database.

    ``pool_pre_ping`` is enabled so a pooled connection that the server closed
    is replaced transparently instead of failing the next request.

    Raises:
        ValueError: the settings do not describe a complete database URL.
    """
    return create_engine(sqlalchemy_url(settings), pool_pre_ping=True)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to ``engine``.

    Sessions are created with ``expire_on_commit=False`` so a caller can read
    attributes of an object after committing it.
    """
    return sessionmaker(bind=engine, expire_on_commit=False)
