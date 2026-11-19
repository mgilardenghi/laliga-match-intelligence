"""Integration tests for the live-prediction migration ``0003``.

The revision adds ``prediction_runs``, ``predictions``, ``simulation_runs`` and
``simulation_team_results`` to the schema of revision ``0002``, extends
``matches`` and ``ingestion_runs``, rewrites two ``matches`` CHECK constraints
and installs the trigger functions that guard a stored prediction. These tests
upgrade a throwaway PostgreSQL to ``0003``, observe the tables, constraints and
indexes the design requires, drive the two rewritten CHECK constraints, list the
triggers and functions of the catalogue, and downgrade back to ``0002``.

The trigger behaviour itself (which insert a guard accepts or rejects) is
covered by the live-prediction tests; this module is structural.

Like ``tests/test_db_integration.py`` these tests need a live PostgreSQL and are
opt-in: ``make test`` deselects them and ``make test-integration`` starts a
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
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

POSTGRES_ENVIRONMENT_VARIABLES = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)
"""The variables the fixture requires; a missing one is a hard failure."""

LIVE_TABLES = frozenset(
    {"prediction_runs", "predictions", "simulation_runs", "simulation_team_results"}
)
"""The tables revision ``0003`` adds."""

GUARD_FUNCTIONS = frozenset(
    {"lmi_prediction_runs_guard", "lmi_predictions_guard_insert", "lmi_reject_mutation"}
)
"""The trigger functions revision ``0003`` installs."""

GUARD_TRIGGERS = {
    "predictions": {
        "predictions_guard_insert",
        "predictions_immutable",
        "predictions_no_truncate",
    },
    "prediction_runs": {"prediction_runs_guard", "prediction_runs_no_truncate"},
}
"""The triggers revision ``0003`` puts on the two guard-protected tables."""

_INSERT_SEASON = """
INSERT INTO seasons (code, start_year, fd_code) VALUES ('2024-25', 2024, '2024-2025')
RETURNING id
"""
"""One season row, so a match row has a valid ``season_id``."""


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


def index_definitions(engine: Engine, table_name: str) -> dict[str, str]:
    """Return every index of ``table_name``, mapped to its definition."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = :table_name"
            ),
            {"table_name": table_name},
        )
        return {str(name): str(definition) for name, definition in rows}


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


def check_definition(engine: Engine, table_name: str, constraint_name: str) -> str:
    """Return the PostgreSQL definition of one CHECK constraint."""
    with engine.connect() as connection:
        definition = connection.execute(
            text(
                "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint AS c "
                "JOIN pg_class AS t ON t.oid = c.conrelid "
                "JOIN pg_namespace AS n ON n.oid = t.relnamespace "
                "WHERE n.nspname = 'public' AND t.relname = :table_name "
                "AND c.conname = :constraint_name"
            ),
            {"table_name": table_name, "constraint_name": constraint_name},
        ).scalar_one()
    return str(definition)


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


def insert_season_and_teams(engine: Engine) -> tuple[int, int, int]:
    """Insert one season and two teams, and return their ids."""
    with engine.begin() as connection:
        season_id = int(connection.execute(text(_INSERT_SEASON)).scalar_one())
        home = text("INSERT INTO teams (name) VALUES ('Real Test') RETURNING id")
        away = text("INSERT INTO teams (name) VALUES ('Test United') RETURNING id")
        home_team_id = int(connection.execute(home).scalar_one())
        away_team_id = int(connection.execute(away).scalar_one())
    return season_id, home_team_id, away_team_id


def test_upgrade_creates_the_live_tables_and_downgrade_restores_0002(database: Database) -> None:
    upgrade(database, "0003")

    stored_tables = table_names(database.engine)
    assert stored_tables >= LIVE_TABLES

    matches = column_specs(database.engine, "matches")
    assert matches["withdrawn_at"][:2] == ("timestamp with time zone", "YES")
    assert matches["withdrawn_at"][2] is None

    ingestion_runs = column_specs(database.engine, "ingestion_runs")
    assert ingestion_runs["rows_withdrawn"][:2] == ("integer", "NO")
    assert ingestion_runs["rows_withdrawn"][2] == "0"

    assert constraint_names(database.engine, "prediction_runs") == {
        "pk_prediction_runs",
        "ck_prediction_runs_status_valid",
        "ck_prediction_runs_trigger_valid",
        "ck_prediction_runs_horizon_days_in_range",
        "ck_prediction_runs_finished_matches_status",
    }
    assert constraint_names(database.engine, "predictions") == {
        "pk_predictions",
        "fk_predictions_run_id_prediction_runs",
        "fk_predictions_match_id_matches",
        "uq_predictions_match_id_model_as_of_date",
        "ck_predictions_model_valid",
        "ck_predictions_p_home_in_range",
        "ck_predictions_p_draw_in_range",
        "ck_predictions_p_away_in_range",
        "ck_predictions_probs_sum_to_one",
    }
    assert constraint_names(database.engine, "simulation_runs") == {
        "pk_simulation_runs",
        "fk_simulation_runs_season_id_seasons",
        "ck_simulation_runs_n_runs_in_range",
        "ck_simulation_runs_seed_non_negative",
        "ck_simulation_runs_model_valid",
        "ck_simulation_runs_positions_ordered",
        "ck_simulation_runs_relegation_positions_at_least_one",
        "ck_simulation_runs_kind_valid",
        "ck_simulation_runs_trigger_valid",
        "ck_simulation_runs_status_valid",
        "ck_simulation_runs_queued_has_no_start",
        "ck_simulation_runs_finished_matches_status",
    }
    assert constraint_names(database.engine, "simulation_team_results") == {
        "pk_simulation_team_results",
        "fk_simulation_team_results_run_id_simulation_runs",
        "fk_simulation_team_results_team_id_teams",
        "uq_simulation_team_results_run_id_team_id",
        "ck_simulation_team_results_p_title_in_range",
        "ck_simulation_team_results_p_ucl_in_range",
        "ck_simulation_team_results_p_europe_in_range",
        "ck_simulation_team_results_p_relegation_in_range",
    }

    prediction_run_indexes = index_definitions(database.engine, "prediction_runs")
    assert set(prediction_run_indexes) == {"pk_prediction_runs", "ix_prediction_runs_as_of_date"}
    assert "(as_of_date DESC)" in prediction_run_indexes["ix_prediction_runs_as_of_date"]

    prediction_indexes = index_definitions(database.engine, "predictions")
    assert set(prediction_indexes) == {
        "pk_predictions",
        "uq_predictions_match_id_model_as_of_date",
        "ix_predictions_match_id_model_as_of_date",
        "ix_predictions_run_id",
    }
    match_index_definition = prediction_indexes["ix_predictions_match_id_model_as_of_date"]
    assert "(match_id, model, as_of_date DESC)" in match_index_definition

    simulation_indexes = index_definitions(database.engine, "simulation_runs")
    assert set(simulation_indexes) == {
        "pk_simulation_runs",
        "ux_simulation_runs_active",
        "ix_simulation_runs_season_id_as_of_date",
    }
    active_definition = simulation_indexes["ux_simulation_runs_active"]
    assert "(season_id, as_of_date, n_runs, seed, model)" in active_definition
    assert "'queued'" in active_definition
    assert "'running'" in active_definition
    succeeded_definition = simulation_indexes["ix_simulation_runs_season_id_as_of_date"]
    assert "as_of_date DESC" in succeeded_definition
    assert "'succeeded'" in succeeded_definition

    downgrade(database, "0002")

    restored_tables = table_names(database.engine)
    assert not LIVE_TABLES & restored_tables
    assert "withdrawn_at" not in column_specs(database.engine, "matches")
    assert "rows_withdrawn" not in column_specs(database.engine, "ingestion_runs")
    assert not GUARD_FUNCTIONS & function_names(database.engine)

    status_definition = check_definition(database.engine, "matches", "ck_matches_status_valid")
    assert "'scheduled'" in status_definition
    assert "'played'" in status_definition
    assert "withdrawn" not in status_definition
    scheduled_definition = check_definition(
        database.engine, "matches", "ck_matches_scheduled_no_goals"
    )
    assert "withdrawn" not in scheduled_definition


@dataclass(frozen=True)
class CheckViolation:
    """One row that must be rejected by exactly one named CHECK constraint."""

    constraint: str
    statement: str


WITHDRAWN_VIOLATIONS = [
    pytest.param(
        CheckViolation(
            constraint="ck_matches_withdrawn_at_matches_status",
            statement=(
                "INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, status) "
                "VALUES (:season_id, DATE '2024-08-17', :home_team_id, :away_team_id, 'withdrawn')"
            ),
        ),
        id="withdrawn-without-withdrawn-at",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_matches_scheduled_no_goals",
            statement=(
                "INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, "
                "status, withdrawn_at, home_goals, away_goals) "
                "VALUES (:season_id, DATE '2024-08-17', :home_team_id, :away_team_id, "
                "'withdrawn', now(), 1, 0)"
            ),
        ),
        id="withdrawn-with-goals",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_matches_withdrawn_at_matches_status",
            statement=(
                "INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, "
                "status, withdrawn_at) "
                "VALUES (:season_id, DATE '2024-08-17', :home_team_id, :away_team_id, "
                "'scheduled', now())"
            ),
        ),
        id="scheduled-with-withdrawn-at",
    ),
]
"""Negative match inserts, each rejected by exactly one named CHECK constraint."""


@pytest.mark.parametrize("violation", WITHDRAWN_VIOLATIONS)
def test_the_rewritten_matches_checks_reject_bad_rows(
    database: Database, violation: CheckViolation
) -> None:
    upgrade(database, "0003")
    status_definition = check_definition(database.engine, "matches", "ck_matches_status_valid")
    assert "withdrawn" in status_definition

    season_id, home_team_id, away_team_id = insert_season_and_teams(database.engine)

    with pytest.raises(IntegrityError) as rejected, database.engine.begin() as connection:
        connection.execute(
            text(violation.statement),
            {
                "season_id": season_id,
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
            },
        )

    assert violation.constraint in str(rejected.value)


def test_a_withdrawn_match_without_goals_is_accepted(database: Database) -> None:
    upgrade(database, "0003")
    season_id, home_team_id, away_team_id = insert_season_and_teams(database.engine)

    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, "
                "status, withdrawn_at) "
                "VALUES (:season_id, DATE '2024-08-17', :home_team_id, :away_team_id, "
                "'withdrawn', now())"
            ),
            {
                "season_id": season_id,
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
            },
        )

    with database.engine.connect() as connection:
        stored = connection.execute(
            text("SELECT count(*) FROM matches WHERE status = 'withdrawn'")
        ).scalar_one()
    assert stored == 1


def test_the_guards_are_triggers_and_functions_of_the_catalogue(database: Database) -> None:
    upgrade(database, "0003")

    assert function_names(database.engine) >= GUARD_FUNCTIONS
    for table_name, expected in GUARD_TRIGGERS.items():
        assert trigger_names(database.engine, table_name) == expected

    downgrade(database, "0002")

    assert not GUARD_FUNCTIONS & function_names(database.engine)
    assert not table_names(database.engine) & set(GUARD_TRIGGERS)


def test_upgrade_downgrade_upgrade_on_an_empty_database_succeeds(database: Database) -> None:
    upgrade(database, "0003")
    downgrade(database, "0002")
    upgrade(database, "0003")

    assert table_names(database.engine) >= LIVE_TABLES


def test_the_migration_chain_has_exactly_one_head() -> None:
    assert ScriptDirectory(str(MIGRATIONS_DIR)).get_heads() == ["0004"]
