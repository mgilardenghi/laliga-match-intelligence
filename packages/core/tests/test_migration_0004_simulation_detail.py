"""Integration tests for the simulation-detail migration ``0004``.

The revision adds ``simulation_fixtures`` and ``simulation_next_match`` to the
schema of revision ``0003`` and installs one ``BEFORE UPDATE`` trigger on each,
so the detail of a finished simulation run is written once and never rewritten.
These tests upgrade a throwaway PostgreSQL to ``0004``, observe the columns,
constraints and triggers the design requires, drive both immutability triggers,
delete a run to prove the ``ON DELETE CASCADE`` still works, downgrade back to
``0003`` and assert the shared ``lmi_reject_mutation()`` function survives.

Like ``tests/test_migration_0003_live.py`` these tests need a live PostgreSQL and
are opt-in: ``make test`` deselects them and ``make test-integration`` starts a
throwaway container. The fixture fails rather than skips when the environment is
not a disposable database, because a missing database is a configuration error,
not a missing capability. There is no shared ``conftest.py`` in this package, so
the fixture lives here.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass

import alembic.command
import pytest
from alembic.script import ScriptDirectory
from lmi_core.config import Settings
from lmi_core.db.alembic_support import MIGRATIONS_DIR, alembic_config
from lmi_core.db.session import make_engine
from lmi_core.db.testing import require_disposable_database
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration

POSTGRES_ENVIRONMENT_VARIABLES = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)
"""The variables the fixture requires; a missing one is a hard failure."""

DETAIL_TABLES = frozenset({"simulation_fixtures", "simulation_next_match"})
"""The tables revision ``0004`` adds."""

DETAIL_TRIGGERS = {
    "simulation_fixtures": {"simulation_fixtures_immutable"},
    "simulation_next_match": {"simulation_next_match_immutable"},
}
"""The triggers revision ``0004`` puts on the two detail tables."""

TRIGGERS_0003 = {
    "predictions": {
        "predictions_guard_insert",
        "predictions_immutable",
        "predictions_no_truncate",
    },
    "prediction_runs": {"prediction_runs_guard", "prediction_runs_no_truncate"},
}
"""The triggers revision ``0003`` installs, which the downgrade must keep."""

SHARED_FUNCTION = "lmi_reject_mutation"
"""The trigger function revision ``0003`` owns and revision ``0004`` reuses."""

_RUN_ID = "00000000-0000-0000-0000-000000000040"
"""The simulation run row the detail rows reference; never a real identifier."""

_INSERT_SEASON = """
INSERT INTO seasons (code, start_year, fd_code) VALUES ('2024-25', 2024, '2024-2025')
RETURNING id
"""
"""One season row, so a match row has a valid ``season_id``."""

_INSERT_MATCH = """
INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, status)
VALUES (:season_id, DATE '2025-03-01', :home_team_id, :away_team_id, 'scheduled')
RETURNING id
"""
"""One scheduled match row, so a detail row has a valid ``match_id``."""

_INSERT_RUN = """
INSERT INTO simulation_runs
    (id, season_id, as_of_date, n_runs, seed, model, ucl_positions, europe_positions,
     relegation_positions, kind, trigger, status, started_at, finished_at, code_version)
VALUES
    (:run_id, :season_id, DATE '2025-03-01', 1000, 42, 'dixon_coles', 4, 6, 3,
     'live', 'cli', 'succeeded', now(), now(), '0.1.0')
"""
"""One finished simulation run, so a detail row has a valid ``run_id``."""

_INSERT_FIXTURE = """
INSERT INTO simulation_fixtures (run_id, match_id, p_home, p_draw, p_away)
VALUES (:run_id, :match_id, 0.5, 0.3, 0.2)
"""
"""One fixture probability row whose triple sums to one."""

_INSERT_NEXT_MATCH = """
INSERT INTO simulation_next_match
    (run_id, team_id, match_id, outcome, n_outcome, n_relegated, n_europe, n_title, points_sum)
VALUES (:run_id, :team_id, :match_id, 'win', 500, 20, 120, 30, 123456)
"""
"""One next-match count row whose qualifier counts stay inside ``n_outcome``."""


@dataclass(frozen=True)
class Database:
    """The throwaway database and the settings that reach it."""

    engine: Engine
    settings: Settings


@pytest.fixture
def database() -> Iterator[Database]:
    """Yield an engine and the settings, with an empty ``public`` schema.

    Every test starts from ``DROP SCHEMA public CASCADE`` plus ``CREATE SCHEMA
    public``, so the tests do not depend on each other or on their order.

    Raises:
        pytest.fail: the destructive-test guard refused the environment, or one of
            the ``POSTGRES_*`` variables is missing. The message names the
            variable to set and never a value.
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
        yield Database(engine=engine, settings=settings)
    finally:
        engine.dispose()


def upgrade(database: Database, revision: str) -> None:
    """Move the fixture database up to ``revision`` through the packaged scripts."""
    alembic.command.upgrade(alembic_config(database.settings), revision)


def downgrade(database: Database, revision: str) -> None:
    """Move the fixture database down to ``revision`` through the packaged scripts."""
    alembic.command.downgrade(alembic_config(database.settings), revision)


def table_names(engine: Engine) -> set[str]:
    """Return the table names in the ``public`` schema."""
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        }


def column_specs(engine: Engine, table_name: str) -> dict[str, tuple[str, str, str | None]]:
    """Return every column of a table, mapped to type, nullability and default."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table_name"
            ),
            {"table_name": table_name},
        )
        specs: dict[str, tuple[str, str, str | None]] = {}
        for name, data_type, nullable, default in rows:
            specs[str(name)] = (
                str(data_type),
                str(nullable),
                None if default is None else str(default),
            )
        return specs


def constraint_names(engine: Engine, table_name: str) -> set[str]:
    """Return the primary key, unique, foreign key and CHECK constraints of a table.

    ``contype = 'n'`` is filtered out: PostgreSQL 18 records a NOT NULL column
    constraint there too, and this test asserts the constraints the design names.
    """
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.execute(
                text(
                    "SELECT c.conname FROM pg_constraint AS c "
                    "JOIN pg_class AS t ON t.oid = c.conrelid "
                    "JOIN pg_namespace AS n ON n.oid = t.relnamespace "
                    "WHERE n.nspname = 'public' AND t.relname = :table_name "
                    "AND c.contype IN ('p', 'u', 'f', 'c')"
                ),
                {"table_name": table_name},
            ).scalars()
        }


def trigger_names(engine: Engine, table_name: str) -> set[str]:
    """Return the names of the triggers on ``table_name``."""
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.execute(
                text(
                    "SELECT g.tgname FROM pg_trigger AS g "
                    "JOIN pg_class AS c ON c.oid = g.tgrelid "
                    "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relname = :table_name "
                    "AND NOT g.tgisinternal"
                ),
                {"table_name": table_name},
            ).scalars()
        }


def schema_trigger_names(engine: Engine) -> set[str]:
    """Return the names of every trigger of the ``public`` schema."""
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.execute(
                text(
                    "SELECT g.tgname FROM pg_trigger AS g "
                    "JOIN pg_class AS c ON c.oid = g.tgrelid "
                    "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND NOT g.tgisinternal"
                )
            ).scalars()
        }


def function_names(engine: Engine) -> set[str]:
    """Return the names of the functions and procedures in the ``public`` schema."""
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.execute(
                text(
                    "SELECT p.proname FROM pg_proc AS p "
                    "JOIN pg_namespace AS n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public'"
                )
            ).scalars()
        }


def row_count(engine: Engine, table_name: str) -> int:
    """Return the number of rows of ``table_name``.

    The table name comes from this module, never from user input.
    """
    with engine.connect() as connection:
        return int(connection.execute(text(f"SELECT count(*) FROM {table_name}")).scalar_one())


def insert_detail_rows(engine: Engine) -> None:
    """Insert a season, two teams, one match, one run and one row of each detail table."""
    with engine.begin() as connection:
        season_id = int(connection.execute(text(_INSERT_SEASON)).scalar_one())
        home_team_id = int(
            connection.execute(
                text("INSERT INTO teams (name) VALUES ('Test Home') RETURNING id")
            ).scalar_one()
        )
        away_team_id = int(
            connection.execute(
                text("INSERT INTO teams (name) VALUES ('Test Away') RETURNING id")
            ).scalar_one()
        )
        match_id = int(
            connection.execute(
                text(_INSERT_MATCH),
                {
                    "season_id": season_id,
                    "home_team_id": home_team_id,
                    "away_team_id": away_team_id,
                },
            ).scalar_one()
        )
        connection.execute(text(_INSERT_RUN), {"run_id": _RUN_ID, "season_id": season_id})
        connection.execute(text(_INSERT_FIXTURE), {"run_id": _RUN_ID, "match_id": match_id})
        connection.execute(
            text(_INSERT_NEXT_MATCH),
            {"run_id": _RUN_ID, "team_id": home_team_id, "match_id": match_id},
        )


def test_upgrade_creates_the_detail_tables(database: Database) -> None:
    upgrade(database, "head")

    assert table_names(database.engine) >= DETAIL_TABLES

    fixtures_specs = column_specs(database.engine, "simulation_fixtures")
    fixtures = {name: spec[:2] for name, spec in fixtures_specs.items()}
    assert fixtures == {
        "run_id": ("uuid", "NO"),
        "match_id": ("integer", "NO"),
        "p_home": ("double precision", "NO"),
        "p_draw": ("double precision", "NO"),
        "p_away": ("double precision", "NO"),
    }
    assert all(spec[2] is None for spec in fixtures_specs.values())

    next_match_specs = column_specs(database.engine, "simulation_next_match")
    next_match = {name: spec[:2] for name, spec in next_match_specs.items()}
    assert next_match == {
        "run_id": ("uuid", "NO"),
        "team_id": ("integer", "NO"),
        "match_id": ("integer", "NO"),
        "outcome": ("text", "NO"),
        "n_outcome": ("integer", "NO"),
        "n_relegated": ("integer", "NO"),
        "n_europe": ("integer", "NO"),
        "n_title": ("integer", "NO"),
        "points_sum": ("bigint", "NO"),
    }
    assert all(spec[2] is None for spec in next_match_specs.values())


def test_the_detail_tables_carry_the_constraints_and_triggers_of_the_design(
    database: Database,
) -> None:
    upgrade(database, "0004")

    assert constraint_names(database.engine, "simulation_fixtures") == {
        "pk_simulation_fixtures",
        "fk_simulation_fixtures_run_id_simulation_runs",
        "fk_simulation_fixtures_match_id_matches",
        "ck_simulation_fixtures_p_home_in_range",
        "ck_simulation_fixtures_p_draw_in_range",
        "ck_simulation_fixtures_p_away_in_range",
        "ck_simulation_fixtures_probs_sum_to_one",
    }
    assert constraint_names(database.engine, "simulation_next_match") == {
        "pk_simulation_next_match",
        "fk_simulation_next_match_run_id_simulation_runs",
        "fk_simulation_next_match_team_id_teams",
        "fk_simulation_next_match_match_id_matches",
        "ck_simulation_next_match_outcome_valid",
        "ck_simulation_next_match_n_outcome_non_negative",
        "ck_simulation_next_match_n_relegated_in_range",
        "ck_simulation_next_match_n_europe_in_range",
        "ck_simulation_next_match_n_title_in_range",
        "ck_simulation_next_match_points_sum_non_negative",
    }

    for table_name, expected in DETAIL_TRIGGERS.items():
        assert trigger_names(database.engine, table_name) == expected
    assert schema_trigger_names(database.engine) >= {
        trigger for expected in DETAIL_TRIGGERS.values() for trigger in expected
    }


UPDATE_STATEMENTS = [
    pytest.param(
        "simulation_fixtures",
        "UPDATE simulation_fixtures SET p_home = 0.6",
        id="fixture-probability",
    ),
    pytest.param(
        "simulation_next_match",
        "UPDATE simulation_next_match SET n_outcome = 501",
        id="next-match-count",
    ),
]
"""One row update per detail table, each must hit the immutability trigger."""


@pytest.mark.parametrize(("table_name", "statement"), UPDATE_STATEMENTS)
def test_an_update_of_a_detail_row_raises_the_immutability_error(
    database: Database, table_name: str, statement: str
) -> None:
    upgrade(database, "0004")
    insert_detail_rows(database.engine)

    with pytest.raises(DBAPIError) as rejected, database.engine.begin() as connection:
        connection.execute(text(statement))

    sqlstate: str | None = getattr(rejected.value.orig, "sqlstate", None)
    assert sqlstate == "P0001"
    message = str(rejected.value.orig)
    assert "immutable" in message
    assert table_name in message


def test_a_delete_of_the_run_cascades_its_detail_rows(database: Database) -> None:
    upgrade(database, "0004")
    insert_detail_rows(database.engine)
    assert row_count(database.engine, "simulation_fixtures") == 1
    assert row_count(database.engine, "simulation_next_match") == 1

    with database.engine.begin() as connection:
        connection.execute(text("DELETE FROM simulation_runs"))

    assert row_count(database.engine, "simulation_fixtures") == 0
    assert row_count(database.engine, "simulation_next_match") == 0


def test_downgrade_removes_the_detail_tables_and_keeps_the_shared_guard(
    database: Database,
) -> None:
    upgrade(database, "0004")
    downgrade(database, "0003")

    assert not DETAIL_TABLES & table_names(database.engine)
    dropped = {trigger for expected in DETAIL_TRIGGERS.values() for trigger in expected}
    assert not dropped & schema_trigger_names(database.engine)

    assert SHARED_FUNCTION in function_names(database.engine)
    for table_name, expected in TRIGGERS_0003.items():
        assert trigger_names(database.engine, table_name) == expected


def test_upgrade_downgrade_upgrade_on_an_empty_database_succeeds(database: Database) -> None:
    upgrade(database, "0004")
    downgrade(database, "0003")
    upgrade(database, "0004")

    assert table_names(database.engine) >= DETAIL_TABLES
    for table_name, expected in DETAIL_TRIGGERS.items():
        assert trigger_names(database.engine, table_name) == expected


def test_the_migration_chain_has_exactly_one_head() -> None:
    assert ScriptDirectory(str(MIGRATIONS_DIR)).get_heads() == ["0004"]
