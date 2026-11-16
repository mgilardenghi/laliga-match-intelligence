"""Tests for the Celery application, its tasks and the stale-run sweep.

The unit tests need neither Redis nor PostgreSQL. Building the Celery
application and reading its configuration opens no connection, and the tasks
are checked by name and configuration rather than by sending a message. One
test re-imports the module with ``Celery.connection`` replaced, which proves
that the import path itself never asks for a broker.

The integration tests use the migrated, seeded, disposable database of the
worker conftest. Each of them inserts one synthetic season and builds its runs
with :func:`lmi_core.simulation.service.create_run`, then backdates the row with
one SQL statement, so the sweep is exercised against the real CHECK constraints
and the real database clock.
"""

import datetime
import importlib
import uuid

import pytest
from celery.signals import worker_ready
from conftest import MigratedDatabase
from kombu.connection import Connection
from lmi_core.db.models import Season, SimulationRun
from lmi_core.simulation import Qualification
from lmi_core.simulation.service import create_run
from lmi_worker import jobs
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

SEASON_CODE = "2099-00"
"""The invented season the integration fixtures use; no real league label."""

AS_OF = datetime.date(2026, 11, 16)
"""A fixed as-of date so the natural key of a run is identical across a test."""

WORKER_READY_UID = "lmi_worker.requeue_stale"
"""The dispatch id :func:`lmi_worker.jobs.connect_worker_ready` registers."""


def test_celery_settings_match_the_amendment() -> None:
    conf = jobs.app.conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_ignore_result is True
    assert conf.task_serializer == "json"
    assert conf.accept_content == ["json"]
    assert conf.result_serializer == "json"
    assert conf.timezone == "Europe/Madrid"
    assert conf.enable_utc is True
    assert conf.broker_transport_options == {"visibility_timeout": 3600}
    assert conf.task_default_queue == jobs.QUEUE_NAME


def test_tasks_are_registered_under_exactly_their_names() -> None:
    assert jobs.simulate.name == "lmi.simulate"
    assert jobs.daily.name == "lmi.daily"
    assert "lmi.simulate" in jobs.app.tasks
    assert "lmi.daily" in jobs.app.tasks
    assert jobs.app.conf.task_default_queue == "lmi"


def test_beat_runs_daily_at_six_madrid_time() -> None:
    entry = jobs.app.conf.beat_schedule["daily"]
    assert entry["task"] == "lmi.daily"
    schedule = entry["schedule"]
    assert schedule.hour == {6}
    assert schedule.minute == {0}
    assert jobs.app.conf.timezone == "Europe/Madrid"


def test_worker_ready_receiver_is_connected_once() -> None:
    jobs.connect_worker_ready()
    jobs.connect_worker_ready()

    dispatch_uids = [lookup[0] for lookup, _ in worker_ready.receivers]
    assert dispatch_uids.count(WORKER_READY_UID) == 1


def test_importing_jobs_opens_no_broker_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    established: list[object] = []

    def refuse_connection(self: object) -> None:
        established.append(self)
        raise AssertionError("importing lmi_worker.jobs opened a broker connection")

    monkeypatch.setattr(Connection, "_establish_connection", refuse_connection)
    importlib.reload(jobs)

    assert established == []


@pytest.mark.integration
def test_requeue_stale_returns_a_backdated_running_run(
    migrated_engine: MigratedDatabase,
) -> None:
    season_code = _synthetic_season(migrated_engine.factories)
    with migrated_engine.factories() as session:
        run, must_enqueue = _new_run(session, season_code)
        run_id = run.id
    assert must_enqueue is True
    _backdate_running(migrated_engine.engine, run_id)

    enqueued: list[uuid.UUID] = []
    returned = jobs.requeue_stale(migrated_engine.factories, enqueued.append)

    assert returned == [run_id]
    assert enqueued == [run_id]

    with migrated_engine.factories() as session:
        again, must_enqueue_again = _new_run(session, season_code)
    assert again.id == run_id
    assert must_enqueue_again is True


@pytest.mark.integration
def test_requeue_stale_leaves_a_freshly_queued_run_alone(
    migrated_engine: MigratedDatabase,
) -> None:
    season_code = _synthetic_season(migrated_engine.factories)
    with migrated_engine.factories() as session:
        run, _ = _new_run(session, season_code)
        run_id = run.id

    enqueued: list[uuid.UUID] = []
    returned = jobs.requeue_stale(migrated_engine.factories, enqueued.append)

    assert returned == []
    assert enqueued == []
    with migrated_engine.factories() as session:
        assert session.get(SimulationRun, run_id) is not None


@pytest.mark.integration
def test_requeue_stale_never_returns_a_succeeded_run(
    migrated_engine: MigratedDatabase,
) -> None:
    season_code = _synthetic_season(migrated_engine.factories)
    with migrated_engine.factories() as session:
        run, _ = _new_run(session, season_code)
        run_id = run.id
    _mark_succeeded(migrated_engine.engine, run_id)

    enqueued: list[uuid.UUID] = []
    returned = jobs.requeue_stale(migrated_engine.factories, enqueued.append)

    assert returned == []
    assert enqueued == []


def _synthetic_season(factories: sessionmaker[Session]) -> str:
    """Insert one invented season and return its code."""
    with factories() as session:
        session.add(Season(code=SEASON_CODE, start_year=2099, fd_code="9900"))
        session.commit()
    return SEASON_CODE


def _new_run(session: Session, season_code: str) -> tuple[SimulationRun, bool]:
    """Open one run with the parameters a second identical call repeats."""
    return create_run(
        session,
        season_code=season_code,
        as_of_date=AS_OF,
        n_runs=1000,
        seed=0,
        trigger="schedule",
        qualification=Qualification(),
        kind="live",
    )


def _backdate_running(engine: Engine, run_id: uuid.UUID) -> None:
    """Move one row to ``running`` with a start 31 minutes in the past."""
    _execute(
        engine,
        "UPDATE simulation_runs"
        " SET status = 'running', started_at = now() - interval '31 minutes'"
        " WHERE id = :run_id",
        run_id,
    )


def _mark_succeeded(engine: Engine, run_id: uuid.UUID) -> None:
    """Move one row to the terminal ``succeeded`` state the schema requires."""
    _execute(
        engine,
        "UPDATE simulation_runs"
        " SET status = 'succeeded', started_at = now() - interval '31 minutes',"
        " finished_at = now()"
        " WHERE id = :run_id",
        run_id,
    )


def _execute(engine: Engine, statement: str, run_id: uuid.UUID) -> None:
    with engine.begin() as connection:
        connection.execute(text(statement), {"run_id": run_id})
