"""Tests for the Celery application, its tasks and the stale-run sweep.

The unit tests need neither Redis nor PostgreSQL. Building the Celery
application and reading its configuration opens no connection, and the tasks
are checked by name and configuration rather than by sending a message. One
test re-imports the module with ``Celery.connection`` replaced, which proves
that the import path itself never asks for a broker.

The integration tests use the migrated, seeded, disposable database of the
worker conftest. Each of them inserts one synthetic season and builds its runs
with :func:`lmi_core.simulation.service.create_run`, then backdates the row's
clocks with SQL statements, so the sweep is exercised against the real CHECK
constraints and the real database clock.
"""

import datetime
import importlib
import uuid

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import worker_ready
from conftest import MigratedDatabase
from kombu.connection import Connection
from lmi_core.db.models import Season, SimulationRun
from lmi_core.modeling.dixon_coles import DixonColesModel
from lmi_core.modeling.elo import ModelFitError
from lmi_core.modeling.types import History
from lmi_core.simulation import Qualification
from lmi_core.simulation.service import STALE_AFTER, create_run
from lmi_worker import jobs
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import InterfaceError, OperationalError
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
    assert conf.task_publish_retry is True
    assert conf.task_publish_retry_policy == jobs.PUBLISH_RETRY_POLICY
    assert conf.task_publish_retry_policy == {
        "max_retries": 3,
        "interval_start": 0.0,
        "interval_step": 0.2,
        "interval_max": 1.0,
    }


def test_tasks_carry_time_limits_and_retry_only_transient_errors() -> None:
    assert jobs.SIMULATE_SOFT_TIME_LIMIT == 240
    assert jobs.SIMULATE_TIME_LIMIT == 300
    assert jobs.DAILY_SOFT_TIME_LIMIT == 420
    assert jobs.DAILY_TIME_LIMIT == 600
    stale_window_seconds = STALE_AFTER.total_seconds()
    assert stale_window_seconds > jobs.SIMULATE_TIME_LIMIT
    assert stale_window_seconds > jobs.DAILY_TIME_LIMIT
    transient = (OperationalError, InterfaceError, ConnectionError)
    assert transient == jobs.TRANSIENT_ERRORS
    for task, soft_limit, hard_limit in (
        (jobs.simulate, jobs.SIMULATE_SOFT_TIME_LIMIT, jobs.SIMULATE_TIME_LIMIT),
        (jobs.daily, jobs.DAILY_SOFT_TIME_LIMIT, jobs.DAILY_TIME_LIMIT),
    ):
        assert task.soft_time_limit == soft_limit
        assert task.time_limit == hard_limit
        assert task.autoretry_for == jobs.TRANSIENT_ERRORS
        assert task.max_retries == 2
        assert task.retry_backoff is True
        assert task.retry_backoff_max == 60
        assert task.retry_jitter is True


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
def test_requeue_stale_keeps_sending_after_one_enqueue_fails(
    migrated_engine: MigratedDatabase,
) -> None:
    season_code = _synthetic_season(migrated_engine.factories)
    run_ids: list[uuid.UUID] = []
    with migrated_engine.factories() as session:
        for seed in range(3):
            run, _ = create_run(
                session,
                season_code=season_code,
                as_of_date=AS_OF,
                n_runs=1000,
                seed=seed,
                trigger="schedule",
                qualification=Qualification(),
                kind="live",
            )
            run_ids.append(run.id)
    for run_id in run_ids:
        _backdate_queued(migrated_engine.engine, run_id)

    enqueued: list[uuid.UUID] = []

    def enqueue(run_id: uuid.UUID) -> None:
        if run_id == run_ids[1]:
            raise RuntimeError("the broker refused one message")
        enqueued.append(run_id)

    returned = jobs.requeue_stale(migrated_engine.factories, enqueue)

    assert returned == [run_ids[0], run_ids[2]]
    assert enqueued == returned


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
def test_requeue_stale_never_returns_a_finished_run(
    migrated_engine: MigratedDatabase,
) -> None:
    season_code = _synthetic_season(migrated_engine.factories)
    with migrated_engine.factories() as session:
        succeeded, _ = _new_run(session, season_code)
        failed, _ = create_run(
            session,
            season_code=season_code,
            as_of_date=AS_OF,
            n_runs=1000,
            seed=1,
            trigger="schedule",
            qualification=Qualification(),
            kind="live",
        )
        succeeded_id = succeeded.id
        failed_id = failed.id
    _mark_finished(migrated_engine.engine, succeeded_id, status="succeeded")
    _mark_finished(migrated_engine.engine, failed_id, status="failed")

    enqueued: list[uuid.UUID] = []
    returned = jobs.requeue_stale(migrated_engine.factories, enqueued.append)

    assert returned == []
    assert enqueued == []


@pytest.mark.integration
def test_a_refused_publish_leaves_the_run_queued(
    migrated_engine: MigratedDatabase,
) -> None:
    season_code = _synthetic_season(migrated_engine.factories)
    with migrated_engine.factories() as session:
        run, _ = _new_run(session, season_code)
        run_id = run.id
    _backdate_queued(migrated_engine.engine, run_id, minutes=2)

    attempted: list[uuid.UUID] = []

    def refuse(run_id: uuid.UUID) -> None:
        attempted.append(run_id)
        raise ConnectionError("the broker refused the message")

    # Two minutes old: the sweep selects the row and the broker refuses it. The
    # refused send writes nothing, so the row stays queued.
    assert jobs.requeue_stale(migrated_engine.factories, refuse) == []
    assert attempted == [run_id]

    # Thirty seconds old: too fresh for the next sweep to select it again.
    _set_requested_age(migrated_engine.engine, run_id, seconds=30)
    enqueued: list[uuid.UUID] = []
    assert jobs.requeue_stale(migrated_engine.factories, enqueued.append) == []
    assert enqueued == []

    # Two minutes old again: the next sweep hands it to the broker.
    _backdate_queued(migrated_engine.engine, run_id, minutes=2)
    assert jobs.requeue_stale(migrated_engine.factories, enqueued.append) == [run_id]
    assert enqueued == [run_id]

    with migrated_engine.factories() as session:
        stored = session.get(SimulationRun, run_id)
    assert stored is not None
    assert stored.status == "queued"


@pytest.mark.integration
def test_a_fit_error_leaves_the_simulation_run_failed(
    migrated_engine: MigratedDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    season_code = _synthetic_season(migrated_engine.factories)
    with migrated_engine.factories() as session:
        run, _ = _new_run(session, season_code)
        run_id = run.id

    def refuse_fit(self: DixonColesModel, history: History, cutoff: datetime.date) -> None:
        raise ModelFitError(
            "dixon_coles: the optimizer returned 2 of 22 parameters that are not finite; "
            "optimizer status 1, message 'ABNORMAL_TERMINATION_IN_LNSRCH'"
        )

    monkeypatch.setattr(DixonColesModel, "_fit", refuse_fit)

    jobs.run_simulation_task(str(run_id))

    with migrated_engine.factories() as session:
        stored = session.get(SimulationRun, run_id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.finished_at is not None
    assert stored.error is not None
    assert "not finite" in stored.error


@pytest.mark.integration
def test_a_soft_time_limit_leaves_the_simulation_run_failed(
    migrated_engine: MigratedDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    season_code = _synthetic_season(migrated_engine.factories)
    with migrated_engine.factories() as session:
        run, _ = _new_run(session, season_code)
        run_id = run.id

    def hit_soft_limit(self: DixonColesModel, history: History, cutoff: datetime.date) -> None:
        # Celery's timeout handler raises the exception with no message, so this
        # reproduces the real signal and not a friendlier variant of it.
        raise SoftTimeLimitExceeded

    monkeypatch.setattr(DixonColesModel, "_fit", hit_soft_limit)

    jobs.run_simulation_task(str(run_id))

    with migrated_engine.factories() as session:
        stored = session.get(SimulationRun, run_id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.status != "running"
    assert stored.finished_at is not None
    # The exception carries no message, so `execute_run` stores its class name:
    # the column still names the cause, and the assertion does not pin the text.
    assert stored.error is not None
    assert "SoftTimeLimitExceeded" in stored.error


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


def _backdate_queued(engine: Engine, run_id: uuid.UUID, *, minutes: int = 2) -> None:
    """Move one queued row's request instant into the past."""
    _execute(
        engine,
        "UPDATE simulation_runs"
        " SET requested_at = now() - (:minutes * interval '1 minute')"
        " WHERE id = :run_id",
        run_id,
        minutes=minutes,
    )


def _set_requested_age(engine: Engine, run_id: uuid.UUID, *, seconds: int) -> None:
    """Move one row's request instant exactly ``seconds`` into the past."""
    _execute(
        engine,
        "UPDATE simulation_runs"
        " SET requested_at = now() - (:seconds * interval '1 second')"
        " WHERE id = :run_id",
        run_id,
        seconds=seconds,
    )


def _mark_finished(engine: Engine, run_id: uuid.UUID, *, status: str) -> None:
    """Move one row to a terminal state with every stale clock in the past."""
    _execute(
        engine,
        "UPDATE simulation_runs"
        " SET status = :status, requested_at = now() - interval '2 minutes',"
        " started_at = now() - interval '31 minutes', finished_at = now()"
        " WHERE id = :run_id",
        run_id,
        status=status,
    )


def _execute(engine: Engine, statement: str, run_id: uuid.UUID, **parameters: int | str) -> None:
    with engine.begin() as connection:
        connection.execute(text(statement), {"run_id": run_id, **parameters})
