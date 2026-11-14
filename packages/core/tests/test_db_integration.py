"""Integration tests for the schema, the migration and the seed.

These tests need a live PostgreSQL and are opt-in. ``make test`` and the unit
step in CI select ``-m "not integration"``, which skips every test in this
module; ``make test-integration`` starts a throwaway ``lmi-test-db`` database on
host port 55432, exports the ``POSTGRES_*`` variables and runs
``pytest -m integration``. CI runs the same selection against its service
database.

The tests drive the real command ``python -m lmi_core.db migrate`` in a
subprocess, so they cover the migration, the seed and the exit code that gates
the Compose stack, not only the ORM metadata. They reproduce the evidence of a
correct deployment: eight domain tables plus ``alembic_version``, 22 seasons,
one team per distinct canonical name, one alias row per CSV row, a second run
that inserts nothing, and a conflicting alias remap that fails without
rewriting anything.

Every test gets an empty ``public`` schema, so the tests are independent of each
other and of their order. Nothing here prints the connection URL or a password:
an assertion message renders the command output with URL credentials redacted.

The ``empty_database`` fixture first calls
:func:`lmi_core.db.testing.require_disposable_database` and refuses to run unless
``LMI_ALLOW_DESTRUCTIVE_TESTS=1`` and ``DATABASE_URL`` is unset, because it drops
the schema it connects to. ``make test-integration`` and the CI integration step
set the opt-in for their throwaway database.
"""

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from lmi_core.config import Settings
from lmi_core.db.models import Season, Team
from lmi_core.db.seed import load_alias_rows
from lmi_core.db.session import make_engine, session_factory
from lmi_core.db.testing import require_disposable_database
from sqlalchemy import func, select, table, text
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

DOMAIN_TABLES = frozenset(
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
"""The eight tables of the schema, excluding ``alembic_version``.

Also the only names :func:`count_rows` accepts, so no table name is interpolated
from free input.
"""

EXPECTED_SEASON_CODES = [
    f"{start_year}-{(start_year + 1) % 100:02d}" for start_year in range(2005, 2027)
]
"""Every season code from ``2005-06`` to ``2026-27``, oldest first."""

_URL_USERINFO = re.compile(r"://[^@\s/]*@")

_INSERT_VALID_MATCH = """
INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, status)
VALUES (:season_id, DATE '2024-08-17', :away_team_id, :home_team_id, 'scheduled')
RETURNING id
"""
"""A valid scheduled match, with home and away swapped on purpose.

The swapped pairing keeps it from colliding on ``uq_matches_season_id_home_team_id_away_team_id``
with a rejected row that the same test inserts; a negative test must fail on the
CHECK constraint under test, not on the unique key.
"""

_INSERT_VALID_ODDS = """
INSERT INTO match_odds (match_id, bookmaker, is_closing, home, draw, away)
VALUES (:match_id, 'bet365', false, 2.100, 3.400, 4.200)
"""


def redacted_output(completed: subprocess.CompletedProcess[str]) -> str:
    """Return the command output of ``completed`` with URL credentials removed."""
    output = (
        f"exit {completed.returncode}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return _URL_USERINFO.sub("://<redacted>@", output)


def run_migrate() -> subprocess.CompletedProcess[str]:
    """Run ``python -m lmi_core.db migrate`` with the parent environment."""
    return subprocess.run(
        [sys.executable, "-m", "lmi_core.db", "migrate"],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )


def count_rows(engine: Engine, table_name: str) -> int:
    """Return the row count of ``table_name``.

    Raises:
        ValueError: ``table_name`` is not one of :data:`DOMAIN_TABLES`. The name
            is never taken from free input.
    """
    if table_name not in DOMAIN_TABLES:
        raise ValueError(f"unknown table {table_name!r}, add it to DOMAIN_TABLES first")
    with engine.connect() as connection:
        count = connection.execute(select(func.count()).select_from(table(table_name))).scalar_one()
    return int(count)


def row_counts(engine: Engine) -> dict[str, int]:
    """Return the row count of every domain table."""
    return {name: count_rows(engine, name) for name in sorted(DOMAIN_TABLES)}


def season_codes(engine: Engine) -> list[str]:
    """Return the stored season codes, oldest first."""
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT code FROM seasons ORDER BY start_year"))
        return [str(code) for code in rows.scalars()]


def stored_alias_team(engine: Engine, source: str, alias: str) -> str:
    """Return the canonical team name the stored ``(source, alias)`` maps to."""
    with engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT teams.name FROM team_aliases "
                "JOIN teams ON teams.id = team_aliases.team_id "
                "WHERE team_aliases.source = :source AND team_aliases.alias = :alias"
            ),
            {"source": source, "alias": alias},
        ).scalar_one()
    return str(stored)


@pytest.fixture
def empty_database() -> Iterator[tuple[Engine, Settings]]:
    """Yield an engine and the settings, with an empty ``public`` schema.

    Every test starts from ``DROP SCHEMA public CASCADE`` plus ``CREATE SCHEMA
    public``, so the tests do not depend on each other or on their order.

    Raises:
        pytest.fail: the destructive-test guard refused the environment, or one of
            the ``POSTGRES_*`` variables is missing. The test is not skipped: an
            integration run without a database is a configuration error, and the
            message names the variable to set.
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
        yield engine, settings
    finally:
        engine.dispose()


@dataclass(frozen=True)
class Seeded:
    """The migrated, seeded database plus the ids the inserts need."""

    engine: Engine
    season_id: int
    home_team_id: int
    away_team_id: int


@pytest.fixture
def seeded(empty_database: tuple[Engine, Settings]) -> Seeded:
    """Migrate and seed an empty database, then return ids from the seed."""
    engine, _settings = empty_database
    completed = run_migrate()
    assert completed.returncode == 0, redacted_output(completed)

    factories = session_factory(engine)
    with factories.begin() as session:
        season = session.scalars(select(Season).order_by(Season.start_year).limit(1)).one()
        teams = session.scalars(select(Team).order_by(Team.id).limit(2)).all()
        if len(teams) != 2:
            pytest.fail("the seed inserted fewer than two teams")
        return Seeded(
            engine=engine,
            season_id=season.id,
            home_team_id=teams[0].id,
            away_team_id=teams[1].id,
        )


def insert_valid_match(seeded: Seeded) -> int:
    """Insert a valid scheduled match and return its id."""
    with seeded.engine.begin() as connection:
        match_id = connection.execute(
            text(_INSERT_VALID_MATCH),
            {
                "season_id": seeded.season_id,
                "home_team_id": seeded.home_team_id,
                "away_team_id": seeded.away_team_id,
            },
        ).scalar_one()
    return int(match_id)


def test_migrate_creates_the_schema_and_is_idempotent(
    empty_database: tuple[Engine, Settings],
) -> None:
    engine, _settings = empty_database

    first = run_migrate()
    assert first.returncode == 0, redacted_output(first)

    with engine.connect() as connection:
        stored_tables = {
            str(name)
            for name in connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        }
        stored_indexes = {
            str(name)
            for name in connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            ).scalars()
        }
    assert stored_tables - {"alembic_version"} == DOMAIN_TABLES
    assert "ix_ingestion_rejections_run_id" in stored_indexes

    counts = row_counts(engine)
    assert counts["seasons"] == 22
    assert season_codes(engine) == EXPECTED_SEASON_CODES

    alias_rows = load_alias_rows()
    assert counts["teams"] == len({row.team for row in alias_rows})
    assert counts["team_aliases"] == len(alias_rows)

    second = run_migrate()
    assert second.returncode == 0, redacted_output(second)
    assert row_counts(engine) == counts


def test_conflicting_alias_remap_fails(empty_database: tuple[Engine, Settings]) -> None:
    engine, _settings = empty_database
    first = run_migrate()
    assert first.returncode == 0, redacted_output(first)

    with engine.begin() as connection:
        wrong_team_id = connection.execute(
            text("INSERT INTO teams (name) VALUES ('Wrong FC') RETURNING id")
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE team_aliases SET team_id = :wrong_team_id "
                "WHERE source = 'football_data' AND alias = 'Betis'"
            ),
            {"wrong_team_id": wrong_team_id},
        )

    second = run_migrate()

    assert second.returncode != 0, redacted_output(second)
    assert "Betis" in redacted_output(second)
    assert stored_alias_team(engine, "football_data", "Betis") == "Wrong FC"


@dataclass(frozen=True)
class CheckViolation:
    """One row that must be rejected by exactly one named CHECK constraint."""

    constraint: str
    statement: str
    values: dict[str, object] = field(default_factory=dict)


CHECK_VIOLATIONS = [
    pytest.param(
        CheckViolation(
            constraint="ck_matches_scheduled_no_goals",
            statement=(
                "INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, "
                "status, home_goals, away_goals) "
                "VALUES (:season_id, DATE '2024-08-17', :home_team_id, :away_team_id, "
                "'scheduled', 1, 0)"
            ),
        ),
        id="scheduled-match-with-goals",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_matches_played_complete",
            statement=(
                "INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, status) "
                "VALUES (:season_id, DATE '2024-08-17', :home_team_id, :away_team_id, 'played')"
            ),
        ),
        id="played-match-without-result",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_matches_result_matches_score",
            statement=(
                "INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, "
                "status, home_goals, away_goals, result) "
                "VALUES (:season_id, DATE '2024-08-17', :home_team_id, :away_team_id, "
                "'played', 2, 1, 'A')"
            ),
        ),
        id="result-does-not-match-score",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_matches_distinct_teams",
            statement=(
                "INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, status) "
                "VALUES (:season_id, DATE '2024-08-17', :home_team_id, :home_team_id, 'scheduled')"
            ),
        ),
        id="home-team-is-away-team",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_matches_home_goals_non_negative",
            statement=(
                "INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, "
                "status, home_goals, away_goals, result) "
                "VALUES (:season_id, DATE '2024-08-17', :home_team_id, :away_team_id, "
                "'played', -1, 1, 'A')"
            ),
        ),
        id="negative-home-goals",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_matches_status_valid",
            statement=(
                "INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, status) "
                "VALUES (:season_id, DATE '2024-08-17', :home_team_id, :away_team_id, 'postponed')"
            ),
        ),
        id="unknown-status",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_match_odds_home_price_above_one",
            statement=(
                "INSERT INTO match_odds (match_id, bookmaker, is_closing, home, draw, away) "
                "VALUES (:match_id, 'bet365', false, 1.000, 3.400, 4.200)"
            ),
        ),
        id="odds-home-price-of-one",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_match_odds_bookmaker_valid",
            statement=(
                "INSERT INTO match_odds (match_id, bookmaker, is_closing, home, draw, away) "
                "VALUES (:match_id, 'betfair', false, 2.100, 3.400, 4.200)"
            ),
        ),
        id="unknown-bookmaker",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_team_aliases_source_valid",
            statement=(
                "INSERT INTO team_aliases (team_id, source, alias) "
                "VALUES (:home_team_id, 'another_source', 'Some Alias')"
            ),
        ),
        id="unknown-alias-source",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_matches_ht_goals_both_or_neither",
            statement=(
                "INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, "
                "status, home_goals, away_goals, ht_away_goals, result) "
                "VALUES (:season_id, DATE '2024-08-17', :home_team_id, :away_team_id, "
                "'played', 1, 0, 0, 'H')"
            ),
        ),
        id="half-time-goals-half-set",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_matches_ht_goals_not_above_ft",
            statement=(
                "INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, "
                "status, home_goals, away_goals, ht_home_goals, ht_away_goals, result) "
                "VALUES (:season_id, DATE '2024-08-17', :home_team_id, :away_team_id, "
                "'played', 1, 0, 3, 0, 'H')"
            ),
        ),
        id="half-time-goals-above-full-time",
    ),
    pytest.param(
        CheckViolation(
            constraint="ck_matches_ht_goals_not_above_ft",
            statement=(
                "INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, "
                "status, home_goals, away_goals, ht_home_goals, ht_away_goals, result) "
                "VALUES (:season_id, DATE '2024-08-17', :home_team_id, :away_team_id, "
                "'played', 1, 1, 0, 2, 'D')"
            ),
        ),
        id="half-time-away-goals-above-full-time",
    ),
]
"""Negative inserts, each rejected by exactly one named CHECK constraint."""


@pytest.mark.parametrize("violation", CHECK_VIOLATIONS)
def test_check_constraints_reject_bad_rows(seeded: Seeded, violation: CheckViolation) -> None:
    match_id = insert_valid_match(seeded)
    values: dict[str, object] = {
        "season_id": seeded.season_id,
        "home_team_id": seeded.home_team_id,
        "away_team_id": seeded.away_team_id,
        "match_id": match_id,
        **violation.values,
    }

    with pytest.raises(IntegrityError) as rejected, seeded.engine.begin() as connection:
        connection.execute(text(violation.statement), values)

    assert violation.constraint in str(rejected.value)


def test_valid_match_and_odds_are_accepted_and_duplicates_rejected(seeded: Seeded) -> None:
    match_id = insert_valid_match(seeded)
    with seeded.engine.begin() as connection:
        connection.execute(text(_INSERT_VALID_ODDS), {"match_id": match_id})

    assert count_rows(seeded.engine, "matches") == 1
    assert count_rows(seeded.engine, "match_odds") == 1

    with pytest.raises(IntegrityError) as rejected, seeded.engine.begin() as connection:
        connection.execute(
            text(_INSERT_VALID_MATCH),
            {
                "season_id": seeded.season_id,
                "home_team_id": seeded.home_team_id,
                "away_team_id": seeded.away_team_id,
            },
        )

    assert "uq_matches_season_id_home_team_id_away_team_id" in str(rejected.value)


def test_running_ingestion_run_with_finished_at_is_rejected(seeded: Seeded) -> None:
    statement = text(
        "INSERT INTO ingestion_runs "
        "(id, source, season_code, loader_version, status, finished_at) "
        "VALUES ('00000000-0000-0000-0000-000000000001', 'football_data', "
        "'2024-25', 'test', 'running', now())"
    )

    with pytest.raises(IntegrityError) as rejected, seeded.engine.begin() as connection:
        connection.execute(statement)

    assert "ck_ingestion_runs_finished_matches_status" in str(rejected.value)


def test_finished_ingestion_run_without_finished_at_is_rejected(seeded: Seeded) -> None:
    statement = text(
        "INSERT INTO ingestion_runs "
        "(id, source, season_code, loader_version, status, finished_at) "
        "VALUES ('00000000-0000-0000-0000-000000000002', 'football_data', "
        "'2024-25', 'test', 'succeeded', NULL)"
    )

    with pytest.raises(IntegrityError) as rejected, seeded.engine.begin() as connection:
        connection.execute(statement)

    assert "ck_ingestion_runs_finished_matches_status" in str(rejected.value)


def test_unknown_ingestion_source_is_rejected(seeded: Seeded) -> None:
    statement = text(
        "INSERT INTO ingestion_runs "
        "(id, source, season_code, loader_version, status) "
        "VALUES ('00000000-0000-0000-0000-000000000003', 'another_source', "
        "'2024-25', 'test', 'running')"
    )

    with pytest.raises(IntegrityError) as rejected, seeded.engine.begin() as connection:
        connection.execute(statement)

    assert "ck_ingestion_runs_source_valid" in str(rejected.value)


def test_valid_played_match_and_ingestion_run_states_are_accepted(seeded: Seeded) -> None:
    with seeded.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO matches (season_id, kickoff_date, home_team_id, away_team_id, "
                "status, home_goals, away_goals, ht_home_goals, ht_away_goals, result) "
                "VALUES (:season_id, DATE '2024-08-17', :home_team_id, :away_team_id, "
                "'played', 2, 1, 1, 1, 'H')"
            ),
            {
                "season_id": seeded.season_id,
                "home_team_id": seeded.home_team_id,
                "away_team_id": seeded.away_team_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO ingestion_runs "
                "(id, source, season_code, loader_version, status) "
                "VALUES ('00000000-0000-0000-0000-000000000004', 'openfootball', "
                "'2024-25', 'test', 'running')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO ingestion_runs "
                "(id, source, season_code, loader_version, status, finished_at) "
                "VALUES ('00000000-0000-0000-0000-000000000005', 'football_data', "
                "'2024-25', 'test', 'succeeded', now())"
            )
        )

    assert count_rows(seeded.engine, "matches") == 1
    assert count_rows(seeded.engine, "ingestion_runs") == 2
