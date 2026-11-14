"""Guards for tests that destroy the database they connect to.

The integration fixture drops and recreates the ``public`` schema, so it must
never point at a database that holds data worth keeping. This module holds the
single check that both the fixture and the operator see when the environment is
not explicitly disposable.

A disposable test database is configured through the ``POSTGRES_*`` variables,
never through ``DATABASE_URL``: a complete URL is the override a deployment
uses, so a set one is treated as a real database and refused.
"""

import os


def require_disposable_database() -> None:
    """Refuse to continue unless the target database is explicitly disposable.

    The caller is about to destroy the schema it connects to. The opt-in
    variable records that a human decided the target is a throwaway database,
    and the absence of ``DATABASE_URL`` keeps the target on the ``POSTGRES_*``
    parts the test runner controls.

    Raises:
        RuntimeError: ``LMI_ALLOW_DESTRUCTIVE_TESTS`` is not ``"1"``, or
            ``DATABASE_URL`` is set to a non-empty value. Each message names
            the environment variable and never a value, so it is safe to log.
    """
    if os.environ.get("LMI_ALLOW_DESTRUCTIVE_TESTS") != "1":
        raise RuntimeError(
            "refusing to run a destructive test: LMI_ALLOW_DESTRUCTIVE_TESTS is not 1; "
            "this suite drops the public schema, so run it with `make test-integration` "
            "or set LMI_ALLOW_DESTRUCTIVE_TESTS=1 for a throwaway database"
        )
    if os.environ.get("DATABASE_URL"):
        raise RuntimeError(
            "refusing to run a destructive test: DATABASE_URL is set; this suite "
            "configures its throwaway database through the POSTGRES_* variables, so "
            "unset DATABASE_URL"
        )
