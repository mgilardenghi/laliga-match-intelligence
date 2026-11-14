"""Guards for tests that destroy the database they connect to.

The integration fixture drops and recreates the ``public`` schema, so it must
never point at a database that holds data worth keeping. This module holds the
single check that both the fixture and the operator see when the environment is
not explicitly disposable.

A disposable test database is configured through the ``POSTGRES_*`` variables,
never through ``DATABASE_URL``: a complete URL is the override a deployment
uses, so a present one is treated as a real database and refused.

The refusal reads ``os.environ`` itself rather than :class:`lmi_core.config.Settings`,
so it happens before any configuration object exists. Because
:class:`pydantic_settings.BaseSettings` matches environment names without regard
to letter case, the check does the same: ``database_url`` set in lower case feeds
``Settings.database_url`` just as ``DATABASE_URL`` does, and an empty value is
still a name the deployment chose to set.
"""

import os

ALLOW_DESTRUCTIVE_TESTS_VARIABLE = "LMI_ALLOW_DESTRUCTIVE_TESTS"
DATABASE_URL_VARIABLE = "DATABASE_URL"


def require_disposable_database() -> None:
    """Refuse to continue unless the target database is explicitly disposable.

    The caller is about to destroy the schema it connects to. The opt-in
    variable records that a human decided the target is a throwaway database,
    and the absence of ``DATABASE_URL`` keeps the target on the ``POSTGRES_*``
    parts the test runner controls.

    Raises:
        RuntimeError: ``LMI_ALLOW_DESTRUCTIVE_TESTS`` is not ``"1"``, or
            ``DATABASE_URL`` is present in the environment under any letter case,
            including with an empty value. Each message names the environment
            variable and never a value, so it is safe to log.
    """
    if os.environ.get(ALLOW_DESTRUCTIVE_TESTS_VARIABLE) != "1":
        raise RuntimeError(
            f"refusing to run a destructive test: {ALLOW_DESTRUCTIVE_TESTS_VARIABLE} is not 1; "
            "this suite drops the public schema, so run it with `make test-integration` "
            f"or set {ALLOW_DESTRUCTIVE_TESTS_VARIABLE}=1 for a throwaway database"
        )
    if any(name.lower() == DATABASE_URL_VARIABLE.lower() for name in os.environ):
        raise RuntimeError(
            f"refusing to run a destructive test: {DATABASE_URL_VARIABLE} is set, in any "
            "letter case; this suite configures its throwaway database through the "
            f"POSTGRES_* variables, so unset {DATABASE_URL_VARIABLE}"
        )
