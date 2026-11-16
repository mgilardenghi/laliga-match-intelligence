"""Tell a transient database failure from a deterministic one.

A job retry only helps when the failure can go away by itself: a connection the
server dropped, a server that is restarting, a deadlock. It cannot fix a wrong
password, a missing database or a bad statement, and retrying those only delays
the failure. SQLAlchemy wraps both kinds in the same classes
(``OperationalError``, ``InterfaceError``), so the decision reads the PostgreSQL
SQLSTATE the driver attached to the original error.

The rule:

- No SQLSTATE at all: the error never reached the server (connection refused,
  host unreachable, socket closed), so it is transient.
- SQLSTATE class ``08`` (connection exception) is transient.
- A short list of server states is transient: ``57P01`` admin shutdown,
  ``57P02`` crash shutdown, ``57P03`` cannot connect now, ``53300`` too many
  connections, ``40001`` serialization failure, ``40P01`` deadlock detected.
- Every other SQLSTATE is deterministic: for example ``28P01`` (invalid
  password), ``28000`` (invalid authorization), ``3D000`` (unknown database) and
  every syntax or constraint error.

A plain :class:`ConnectionError` (a socket-level failure outside the driver) is
transient too. Anything else is not a database failure and is not transient.
"""

from typing import Final

from sqlalchemy.exc import DBAPIError

TRANSIENT_SQLSTATES: Final = frozenset({"57P01", "57P02", "57P03", "53300", "40001", "40P01"})
"""Server SQLSTATEs outside class ``08`` that a retry can outlive."""

CONNECTION_EXCEPTION_CLASS: Final = "08"
"""The SQLSTATE class of connection exceptions, all of them transient."""


def sqlstate_of(error: BaseException) -> str | None:
    """Return the SQLSTATE the driver attached to ``error``, or ``None``.

    A SQLAlchemy ``DBAPIError`` keeps the driver error in ``orig``; psycopg
    exposes the code as ``sqlstate``. A driver error passed directly is read the
    same way.
    """
    original = error.orig if isinstance(error, DBAPIError) else error
    code = getattr(original, "sqlstate", None)
    return code if isinstance(code, str) and code else None


def is_transient_db_error(error: BaseException) -> bool:
    """Return whether a retry could outlive ``error``, by the module rule."""
    if isinstance(error, DBAPIError):
        code = sqlstate_of(error)
        if code is None:
            return True
        return code.startswith(CONNECTION_EXCEPTION_CLASS) or code in TRANSIENT_SQLSTATES
    return isinstance(error, ConnectionError)
