"""Integration tests for the backtest results migration ``0002``.

The revision adds ``backtest_runs``, ``backtest_metrics`` and
``backtest_calibration`` to the schema of revision ``0001``. These tests upgrade a
throwaway PostgreSQL to ``0002``, observe the tables, constraints and indexes the
design requires, insert the rows the CHECK constraints and the partial unique
indexes must reject, and downgrade back to ``0001``.

Like ``tests/test_db_integration.py`` they need a live PostgreSQL and are opt-in:
``make test`` deselects them and ``make test-integration`` starts a throwaway
container. The fixture fails rather than skips when the environment is not a
disposable database, because a missing database is a configuration error, not a
missing capability. There is no shared ``conftest.py`` in this package, so the
fixture lives here.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass

import alembic.command
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from lmi_core.config import Settings
from lmi_core.db import models  # noqa: F401  (registers the tables on Base.metadata)
from lmi_core.db.alembic_support import MIGRATIONS_DIR, alembic_config
from lmi_core.db.base import Base
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

TABLES_0001 = frozenset(
    {
        "ingestion_rejections",
        "ingestion_runs",
        "match_odds",
        "match_stats",
        "matches",
        "seasons",
        "team_aliases",
        "teams",
    }
)
"""The tables revision ``0001`` creates."""

BACKTEST_TABLES = frozenset({"backtest_calibration", "backtest_metrics", "backtest_runs"})
"""The tables revision ``0002`` adds."""

_RUN_ID = "00000000-0000-0000-0000-000000000010"
"""The run row the metric and calibration inserts reference."""

_OTHER_RUN_ID = "00000000-0000-0000-0000-0000000000ff"
"""A second run id, so a rejected run row cannot collide on the primary key."""

_INSERT_RUN = """
INSERT INTO backtest_runs (id, status, seed, from_year, step_days, code_version, config)
VALUES (:run_id, 'running', 42, 2007, 7, '0.1.0', '{}')
"""

_INSERT_SEASON = """
INSERT INTO seasons (code, start_year, fd_code) VALUES ('2024-25', 2024, '2024-2025')
RETURNING id
"""

_INSERT_SEASON_METRIC = """
INSERT INTO backtest_metrics
    (run_id, model, season_id, subset, n_matches, n_fixtures, log_loss, brier, rps, accuracy)
VALUES (:run_id, 'elo', :season_id, 'all', 100, 120, 0.9, 0.55, 0.2, 0.5)
"""

_INSERT_OVERALL_METRIC = """
INSERT INTO backtest_metrics
    (run_id, model, season_id, subset, n_matches, n_fixtures, log_loss, brier, rps, accuracy)
VALUES (:run_id, 'elo', NULL, 'all', 100, 120, 0.9, 0.55, 0.2, 0.5)
"""


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


def insert_run(engine: Engine) -> None:
    """Insert the valid run row the metrics and calibration rows reference."""
    with engine.begin() as connection:
        connection.execute(text(_INSERT_RUN), {"run_id": _RUN_ID})


def insert_season(engine: Engine) -> int:
    """Insert one season and return its id."""
    with engine.begin() as connection:
        return int(connection.execute(text(_INSERT_SEASON)).scalar_one())


def test_upgrade_creates_the_backtest_tables_and_downgrade_restores_0001(
    database: Database,
) -> None:
    upgrade(database, "0001")
    assert table_names(database.engine) == TABLES_0001 | {"alembic_version"}

    upgrade(database, "0002")

    assert table_names(database.engine) == TABLES_0001 | BACKTEST_TABLES | {"alembic_version"}

    assert constraint_names(database.engine, "backtest_runs") == {
        "pk_backtest_runs",
        "ck_backtest_runs_status_valid",
        "ck_backtest_runs_finished_matches_status",
        "ck_backtest_runs_step_days_at_least_one",
    }
    assert constraint_names(database.engine, "backtest_metrics") == {
        "pk_backtest_metrics",
        "fk_backtest_metrics_run_id_backtest_runs",
        "fk_backtest_metrics_season_id_seasons",
        "ck_backtest_metrics_model_valid",
        "ck_backtest_metrics_subset_valid",
        "ck_backtest_metrics_n_matches_positive",
        "ck_backtest_metrics_n_fixtures_at_least_n_matches",
        "ck_backtest_metrics_log_loss_non_negative",
        "ck_backtest_metrics_brier_in_range",
        "ck_backtest_metrics_rps_in_range",
        "ck_backtest_metrics_accuracy_in_range",
    }
    assert constraint_names(database.engine, "backtest_calibration") == {
        "pk_backtest_calibration",
        "fk_backtest_calibration_run_id_backtest_runs",
        "uq_backtest_calibration_run_id_model_outcome_bin_index",
        "ck_backtest_calibration_model_valid",
        "ck_backtest_calibration_outcome_valid",
        "ck_backtest_calibration_bin_index_in_range",
        "ck_backtest_calibration_n_non_negative",
        "ck_backtest_calibration_empty_bin_has_no_mean",
        "ck_backtest_calibration_empty_bin_has_no_frequency",
    }

    run_indexes = index_definitions(database.engine, "backtest_runs")
    assert set(run_indexes) == {"pk_backtest_runs", "ix_backtest_runs_status_started_at"}
    assert "(status, started_at DESC)" in run_indexes["ix_backtest_runs_status_started_at"]

    metric_indexes = index_definitions(database.engine, "backtest_metrics")
    assert set(metric_indexes) == {
        "pk_backtest_metrics",
        "ux_backtest_metrics_season",
        "ux_backtest_metrics_overall",
    }
    season_index = metric_indexes["ux_backtest_metrics_season"]
    assert season_index.startswith("CREATE UNIQUE INDEX")
    assert "(run_id, model, subset, season_id)" in season_index
    assert "season_id IS NOT NULL" in season_index
    overall_index = metric_indexes["ux_backtest_metrics_overall"]
    assert overall_index.startswith("CREATE UNIQUE INDEX")
    assert "(run_id, model, subset)" in overall_index
    assert "season_id IS NULL" in overall_index

    downgrade(database, "0001")

    assert table_names(database.engine) == TABLES_0001 | {"alembic_version"}


def test_the_migration_chain_has_exactly_one_head() -> None:
    assert ScriptDirectory(str(MIGRATIONS_DIR)).get_heads() == ["0004"]


@dataclass(frozen=True)
class CheckViolation:
    """One row that must be rejected by exactly one named CHECK constraint."""

    constraint: str
    statement: str


CHECK_VIOLATIONS = [
    pytest.param(
        CheckViolation(
            constraint="ck_backtest_metrics_model_valid",
            statement=(
                "INSERT INTO backtest_metrics "
                "(run_id, model, season_id, subset, n_matches, n_fixtures, "
                "log_loss, brier, rps, accuracy) "
                "VALUES (:run_id, 'gradient_boost', :season_id, 'all', 100, 120, "
                "0.9, 0.55, 0.2, 0.5)"
            ),
        ),
        id="unknown-model",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_backtest_metrics_subset_valid",
            statement=(
                "INSERT INTO backtest_metrics "
                "(run_id, model, season_id, subset, n_matches, n_fixtures, "
                "log_loss, brier, rps, accuracy) "
                "VALUES (:run_id, 'elo', :season_id, 'x', 100, 120, 0.9, 0.55, 0.2, 0.5)"
            ),
        ),
        id="unknown-subset",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_backtest_metrics_n_matches_positive",
            statement=(
                "INSERT INTO backtest_metrics "
                "(run_id, model, season_id, subset, n_matches, n_fixtures, "
                "log_loss, brier, rps, accuracy) "
                "VALUES (:run_id, 'elo', :season_id, 'all', 0, 120, 0.9, 0.55, 0.2, 0.5)"
            ),
        ),
        id="zero-matches",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_backtest_metrics_brier_in_range",
            statement=(
                "INSERT INTO backtest_metrics "
                "(run_id, model, season_id, subset, n_matches, n_fixtures, "
                "log_loss, brier, rps, accuracy) "
                "VALUES (:run_id, 'elo', :season_id, 'all', 100, 120, 0.9, 2.5, 0.2, 0.5)"
            ),
        ),
        id="brier-above-two",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_backtest_runs_finished_matches_status",
            statement=(
                "INSERT INTO backtest_runs "
                "(id, status, finished_at, seed, from_year, step_days, code_version, config) "
                f"VALUES ('{_OTHER_RUN_ID}', 'running', now(), 42, 2007, 7, '0.1.0', '{{}}')"
            ),
        ),
        id="running-run-with-finished-at",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_backtest_calibration_empty_bin_has_no_mean",
            statement=(
                "INSERT INTO backtest_calibration "
                "(run_id, model, outcome, bin_index, bin_lower, bin_upper, n, mean_predicted) "
                "VALUES (:run_id, 'elo', 'H', 0, 0.0, 0.1, 0, 0.5)"
            ),
        ),
        id="empty-calibration-bin-with-mean",
    ),
]
"""Negative inserts, each rejected by exactly one named CHECK constraint."""


@pytest.mark.parametrize("violation", CHECK_VIOLATIONS)
def test_check_constraints_reject_bad_rows(database: Database, violation: CheckViolation) -> None:
    upgrade(database, "head")
    insert_run(database.engine)
    season_id = insert_season(database.engine)

    with pytest.raises(IntegrityError) as rejected, database.engine.begin() as connection:
        connection.execute(
            text(violation.statement),
            {"run_id": _RUN_ID, "season_id": season_id},
        )

    assert violation.constraint in str(rejected.value)


def test_partial_unique_indexes_reject_duplicates_and_accept_both_rows(
    database: Database,
) -> None:
    upgrade(database, "head")
    insert_run(database.engine)
    season_id = insert_season(database.engine)

    with database.engine.begin() as connection:
        connection.execute(text(_INSERT_SEASON_METRIC), {"run_id": _RUN_ID, "season_id": season_id})
        connection.execute(text(_INSERT_OVERALL_METRIC), {"run_id": _RUN_ID})

    with database.engine.connect() as connection:
        stored = connection.execute(text("SELECT count(*) FROM backtest_metrics")).scalar_one()
    assert stored == 2

    with pytest.raises(IntegrityError) as rejected, database.engine.begin() as connection:
        connection.execute(text(_INSERT_OVERALL_METRIC), {"run_id": _RUN_ID})
    assert "ux_backtest_metrics_overall" in str(rejected.value)

    with pytest.raises(IntegrityError) as rejected, database.engine.begin() as connection:
        connection.execute(text(_INSERT_SEASON_METRIC), {"run_id": _RUN_ID, "season_id": season_id})
    assert "ux_backtest_metrics_season" in str(rejected.value)


def test_calibration_unique_constraint_rejects_a_duplicate_bin(database: Database) -> None:
    upgrade(database, "head")
    insert_run(database.engine)

    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO backtest_calibration "
                "(run_id, model, outcome, bin_index, bin_lower, bin_upper, n, "
                "mean_predicted, observed_frequency) "
                "VALUES (:run_id, 'elo', 'H', 0, 0.0, 0.1, 5, 0.05, 0.04)"
            ),
            {"run_id": _RUN_ID},
        )

    with pytest.raises(IntegrityError) as rejected, database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO backtest_calibration "
                "(run_id, model, outcome, bin_index, bin_lower, bin_upper, n, "
                "mean_predicted, observed_frequency) "
                "VALUES (:run_id, 'elo', 'H', 0, 0.0, 0.1, 6, 0.06, 0.05)"
            ),
            {"run_id": _RUN_ID},
        )
    assert "uq_backtest_calibration_run_id_model_outcome_bin_index" in str(rejected.value)


def test_orm_metadata_matches_the_migrated_schema(database: Database) -> None:
    upgrade(database, "head")

    with database.engine.connect() as connection:
        context = MigrationContext.configure(connection)
        differences = compare_metadata(context, Base.metadata)

    assert differences == []
