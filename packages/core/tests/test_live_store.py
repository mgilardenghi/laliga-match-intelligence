"""Integration tests for the live prediction store and the prediction loader.

They run against the throwaway PostgreSQL of :mod:`live_support`, migrated to
``head``, so the insert and update guards of revision ``0003`` are active. Every
match, team and probability here is synthetic.

The store must not massage dates: a prediction is only accepted for a match
dated after Madrid today and an ``as_of`` equal to Madrid today, so each
scenario reads both from one PostgreSQL statement.
"""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from live_support import (  # noqa: F401
    Database,
    database,
    madrid_dates,
    make_prediction_batch,
    store_match,
    store_team,
)
from lmi_core.live import data as live_data
from lmi_core.live import store
from lmi_core.live.store import WriteResult
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

_SELECT_RUN = """
SELECT status, trigger, horizon_days, code_version, data_fingerprint, n_history,
       n_fixtures, n_inserted, n_existing, n_no_forecast, finished_at, error
FROM prediction_runs WHERE id = :id
"""
"""The run row a store test reads back."""

_COUNT_PREDICTIONS = "SELECT count(*) FROM predictions WHERE run_id = :run_id"
"""How many prediction rows one run left behind."""

_UPDATE_MATCH_PLAYED = """
UPDATE matches
SET status = 'played', home_goals = :home_goals, away_goals = :away_goals,
    result = :result, updated_at = now()
WHERE id = :id
"""
"""Turn one stored fixture into a played match, as the results loader would."""

_UPDATE_MATCH_WITHDRAWN = """
UPDATE matches SET status = 'withdrawn', withdrawn_at = now(), updated_at = now() WHERE id = :id
"""
"""Withdraw one stored fixture after its prediction was stored."""


def _fixture(db: Database, *, kickoff_date: date, index: int) -> int:
    """Store one scheduled fixture dated ``kickoff_date`` and return its id."""
    engine = db.engine
    home = store_team(engine, f"Synthetic Home {index}")
    away = store_team(engine, f"Synthetic Away {index}")
    return store_match(
        engine,
        season_id=db.season_id,
        home_team_id=home,
        away_team_id=away,
        kickoff_date=kickoff_date,
    )


def _played(
    engine: Engine,
    match_id: int,
    *,
    home_goals: int,
    away_goals: int,
    result: str,
) -> None:
    """Turn a fixture into a played match with the given scoreline and result."""
    with engine.begin() as connection:
        connection.execute(
            text(_UPDATE_MATCH_PLAYED),
            {
                "id": match_id,
                "home_goals": home_goals,
                "away_goals": away_goals,
                "result": result,
            },
        )


def _withdrawn(engine: Engine, match_id: int) -> None:
    """Withdraw a fixture after its prediction was stored."""
    with engine.begin() as connection:
        connection.execute(text(_UPDATE_MATCH_WITHDRAWN), {"id": match_id})


def _run_row(engine: Engine, run_id: uuid.UUID) -> Any:
    """Read one run row back from the database."""
    with engine.connect() as connection:
        return connection.execute(text(_SELECT_RUN), {"id": run_id}).one()


def _prediction_count(engine: Engine, run_id: uuid.UUID) -> int:
    """Return how many prediction rows one run stored."""
    with engine.connect() as connection:
        return int(connection.execute(text(_COUNT_PREDICTIONS), {"run_id": run_id}).scalar_one())


@pytest.mark.integration
def test_write_batch_stores_the_rows_and_finishes_the_run(database: Database) -> None:  # noqa: F811
    engine = database.engine
    _, today, tomorrow = madrid_dates(engine)
    matches = [
        _fixture(database, kickoff_date=tomorrow, index=1),
        _fixture(database, kickoff_date=tomorrow + timedelta(days=1), index=2),
    ]
    batch = make_prediction_batch(as_of=today, match_ids=matches, models=("home_win", "base_rate"))

    with Session(engine) as session:
        run_id = store.start_run(session, as_of=today, horizon_days=7, code_version="test")
        result = store.write_batch(session, run_id, batch)

    assert result == WriteResult(n_inserted=4, n_existing=0, n_no_forecast=0)
    assert _prediction_count(engine, run_id) == 4

    run = _run_row(engine, run_id)
    assert run.status == "succeeded"
    assert run.finished_at is not None
    assert run.trigger == "cli"
    assert run.horizon_days == 7
    assert run.code_version == "test"
    assert run.data_fingerprint == batch.data_fingerprint
    assert run.n_history == batch.n_history
    assert run.n_fixtures == batch.n_fixtures
    assert run.n_inserted == 4
    assert run.n_existing == 0
    assert run.n_no_forecast == 0
    assert run.error is None


@pytest.mark.integration
def test_write_batch_without_rows_still_finishes_the_run(database: Database) -> None:  # noqa: F811
    engine = database.engine
    _, today, _tomorrow = madrid_dates(engine)
    batch = make_prediction_batch(as_of=today, match_ids=[])

    with Session(engine) as session:
        run_id = store.start_run(session, as_of=today, horizon_days=7, code_version="test")
        result = store.write_batch(session, run_id, batch)

    assert result == WriteResult(n_inserted=0, n_existing=0, n_no_forecast=0)
    assert _prediction_count(engine, run_id) == 0
    assert _run_row(engine, run_id).status == "succeeded"


@pytest.mark.integration
def test_a_second_write_of_the_same_as_of_inserts_nothing(database: Database) -> None:  # noqa: F811
    engine = database.engine
    _, today, tomorrow = madrid_dates(engine)
    matches = [
        _fixture(database, kickoff_date=tomorrow, index=1),
        _fixture(database, kickoff_date=tomorrow + timedelta(days=1), index=2),
    ]
    batch = make_prediction_batch(as_of=today, match_ids=matches)

    with Session(engine) as session:
        first_run = store.start_run(session, as_of=today, horizon_days=7, code_version="test")
        first = store.write_batch(session, first_run, batch)
        second_run = store.start_run(session, as_of=today, horizon_days=7, code_version="test")
        second = store.write_batch(session, second_run, batch)

    assert first.n_inserted == 2
    assert second == WriteResult(n_inserted=0, n_existing=2, n_no_forecast=0)
    assert _prediction_count(engine, first_run) == 2
    assert _prediction_count(engine, second_run) == 0
    assert _run_row(engine, second_run).n_existing == 2


@pytest.mark.integration
def test_fail_run_records_the_error_and_stores_no_prediction(database: Database) -> None:  # noqa: F811
    engine = database.engine
    _, today, _tomorrow = madrid_dates(engine)

    with Session(engine) as session:
        run_id = store.start_run(session, as_of=today, horizon_days=7, code_version="test")
        store.fail_run(session, run_id, error="synthetic failure")

    run = _run_row(engine, run_id)
    assert run.status == "failed"
    assert run.error == "synthetic failure"
    assert run.finished_at is not None
    assert _prediction_count(engine, run_id) == 0


@pytest.mark.integration
def test_write_batch_on_a_finished_run_raises_and_writes_nothing(database: Database) -> None:  # noqa: F811
    engine = database.engine
    _, today, tomorrow = madrid_dates(engine)
    first_match = _fixture(database, kickoff_date=tomorrow, index=1)
    other_match = _fixture(database, kickoff_date=tomorrow + timedelta(days=1), index=2)
    batch = make_prediction_batch(as_of=today, match_ids=[first_match])
    extra = make_prediction_batch(as_of=today, match_ids=[other_match])

    with Session(engine) as session:
        run_id = store.start_run(session, as_of=today, horizon_days=7, code_version="test")
        store.write_batch(session, run_id, batch)
        with pytest.raises(ValueError, match="not 'running'"):
            store.write_batch(session, run_id, extra)

    assert _prediction_count(engine, run_id) == 1
    assert _run_row(engine, run_id).n_inserted == 1


@pytest.mark.integration
def test_fail_run_on_a_finished_run_raises(database: Database) -> None:  # noqa: F811
    engine = database.engine
    _, today, tomorrow = madrid_dates(engine)
    match_id = _fixture(database, kickoff_date=tomorrow, index=1)
    batch = make_prediction_batch(as_of=today, match_ids=[match_id])

    with Session(engine) as session:
        run_id = store.start_run(session, as_of=today, horizon_days=7, code_version="test")
        store.write_batch(session, run_id, batch)
        with pytest.raises(ValueError, match="not 'running'"):
            store.fail_run(session, run_id, error="too late")

    assert _run_row(engine, run_id).status == "succeeded"


@pytest.mark.integration
def test_load_prediction_rows_joins_the_match_state(database: Database) -> None:  # noqa: F811
    engine = database.engine
    _, today, tomorrow = madrid_dates(engine)
    scheduled_id = _fixture(database, kickoff_date=tomorrow, index=1)
    played_id = _fixture(database, kickoff_date=tomorrow + timedelta(days=1), index=2)
    withdrawn_id = _fixture(database, kickoff_date=tomorrow + timedelta(days=2), index=3)
    matches = [scheduled_id, played_id, withdrawn_id]
    batch = make_prediction_batch(as_of=today, match_ids=matches, models=("home_win", "base_rate"))

    with Session(engine) as session:
        run_id = store.start_run(session, as_of=today, horizon_days=7, code_version="test")
        store.write_batch(session, run_id, batch)

    _played(engine, played_id, home_goals=2, away_goals=0, result="H")
    _withdrawn(engine, withdrawn_id)

    with Session(engine) as session:
        rows = live_data.load_prediction_rows(session)
        one_model = live_data.load_prediction_rows(session, model="base_rate")

    assert len(rows) == 6
    by_match = {row.match_id: row for row in rows}
    assert set(by_match) == set(matches)
    assert by_match[scheduled_id].kickoff_date == tomorrow
    assert by_match[scheduled_id].match_status == "scheduled"
    assert by_match[scheduled_id].result is None
    assert by_match[played_id].match_status == "played"
    assert by_match[played_id].result == "H"
    assert by_match[withdrawn_id].match_status == "withdrawn"
    assert by_match[withdrawn_id].result is None

    assert [row.model for row in one_model] == ["base_rate"] * 3
    assert [row.match_id for row in one_model] == matches
    assert [(row.p_home, row.p_draw, row.p_away) for row in one_model] == [(0.5, 0.3, 0.2)] * 3
