"""Tell a transient database failure from a deterministic one.

A job retry only helps when the failure can go away by itself: a connection the
server dropped, a server that is restarting, a deadlock. It cannot fix a wrong
password, a missing database or a bad statement, and retrying those only delays
the failure. SQLAlchemy wraps both kinds in the same classes
(``OperationalError``, ``InterfaceError``), so the decision reads the PostgreSQL
SQLSTATE the driver attached to the original error.

The rule:

- No SQLSTATE at all: libpq raised the error itself, which is what every
  connect-time failure looks like (verified against a live PostgreSQL 18 with
  psycopg 3: a wrong password, an unknown database and a refused port all carry
  ``sqlstate=None``). The libpq message is then the only signal, so a message
  that names a deterministic cause (:data:`DETERMINISTIC_CONNECT_MARKERS`: a
  failed password or authentication, an unknown database or role, a pg_hba
  rejection) is deterministic, and any other connect failure (refused, timed
  out, host unreachable, socket closed) is transient. The markers are the
  English libpq texts the stack runs with; a localized server would make them
  miss, and the failure would then be retried up to the bound.
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

DETERMINISTIC_CONNECT_MARKERS: Final = (
    "password authentication failed",
    "authentication failed",
    "does not exist",
    "no pg_hba.conf entry",
    "role is not permitted to log in",
)
"""libpq message fragments of a connect failure that a retry cannot fix."""

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
            message = str(error.orig).lower()
            return not any(marker in message for marker in DETERMINISTIC_CONNECT_MARKERS)
        return code.startswith(CONNECTION_EXCEPTION_CLASS) or code in TRANSIENT_SQLSTATES
    return isinstance(error, ConnectionError)
