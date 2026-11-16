"""The Celery application: the simulation task, the daily task and the recovery sweep.

A task is a pointer to a row. ``lmi.simulate`` carries a run id and the worker
reads every input from PostgreSQL, so a message never duplicates database state
and a lost message leaves a row the sweep can re-enqueue. For the same reason
the application has no result backend and ignores results: the run row is the
only record of what happened.

The claim in :func:`lmi_core.simulation.service.execute_run` makes the task
idempotent. A worker that receives the same id twice, or a sweep that re-sends a
run another worker already took over, cannot execute it twice; the second claim
matches no row and returns.

Recovery is deliberately bounded, not instant. A redis visibility timeout of
one hour redelivers a message whose worker died; the claim's own 30-minute
window lets a second executor take a ``running`` row over. When no worker
restarts, the boot-time sweep is what closes the gap, and the worst case is the
30-minute stale window stated in the ADR-0004 amendment.

Importing this module builds the Celery application but opens no broker
connection: Celery connects lazily when a task is sent or consumed.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Final

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready
from lmi_core.config import get_settings
from lmi_core.db.models import SimulationRun
from lmi_core.db.session import make_engine, session_factory
from lmi_core.simulation.service import STALE_AFTER, execute_run
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from lmi_worker import daily as daily_cycle

logger = logging.getLogger(__name__)

QUEUE_NAME: Final = "lmi"
"""The one queue the worker consumes; the API sends ``lmi.simulate`` here."""

SIMULATE_TASK: Final = "lmi.simulate"
DAILY_TASK: Final = "lmi.daily"

_QUEUED_STALE_AFTER: Final = timedelta(minutes=1)
"""How long a queued message may wait before the sweep sends it again."""

_WORKER_READY_UID: Final = "lmi_worker.requeue_stale"
"""The dispatch id that makes connecting the signal receiver idempotent."""

app = Celery("lmi", broker=get_settings().redis_url)
app.conf.update(
    # Acknowledging after the work keeps the message when the process dies mid-run.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # One prefetched message at a time: a simulation is CPU-bound and a worker
    # holding several messages would hide them from the other workers.
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Madrid",
    enable_utc=True,
    # One hour, longer than any simulation, so Redis does not redeliver a run
    # whose worker is still computing it.
    broker_transport_options={"visibility_timeout": 3600},
    task_default_queue=QUEUE_NAME,
)

# 06:00 Europe/Madrid. The schedule is what keeps the last pre-kickoff
# forecast: predictions of day D cover only fixtures dated after D, so a match
# dated D is last predicted on D-1, and a skipped daily run leaves that match
# with the previous day's forecast and none from the day itself.
app.conf.beat_schedule = {
    "daily": {
        "task": DAILY_TASK,
        "schedule": crontab(hour=6, minute=0),
    }
}


def run_simulation_task(run_id: str) -> None:
    """Execute one simulation run in this worker process.

    The engine is built for this task and disposed in a ``finally``, so a long
    lived worker holds no connection pool open between jobs. A failure is
    recorded by :func:`execute_run` on the run row and never printed here; a
    driver message can carry the connection URL.
    """
    engine = make_engine(get_settings())
    try:
        execute_run(session_factory(engine), uuid.UUID(run_id))
    finally:
        engine.dispose()


def run_daily_task() -> None:
    """Run the daily cycle in this worker process and log its exit code."""
    exit_code = daily_cycle.run_daily_cycle()
    logger.info("daily: cycle finished with exit code %d", exit_code)


# Registration without the decorator: Celery ships no type information, and
# this form keeps the task functions plain module-level functions.
simulate = app.task(name=SIMULATE_TASK)(run_simulation_task)
daily = app.task(name=DAILY_TASK)(run_daily_task)


def requeue_stale(
    session_factory: sessionmaker[Session],
    enqueue: Callable[[uuid.UUID], None],
) -> list[uuid.UUID]:
    """Send every abandoned active run to ``enqueue`` and return the ids in order.

    One read-only query selects two kinds of row: a ``queued`` run whose
    ``requested_at`` is older than one minute, and a ``running`` run with no
    ``started_at`` or one older than the claim's own :data:`STALE_AFTER` window.
    Both cutoffs are compared with ``now()`` in PostgreSQL, never with the
    process clock, so the decision cannot disagree with the row the claim reads.
    The order is ``requested_at`` then id, which makes the sweep deterministic
    even when two rows share a timestamp.

    Nothing is written. Enqueueing the message is enough, because the claim's
    stale branch in :func:`~lmi_core.simulation.service.execute_run` takes a
    selected row over. A ``running`` row only becomes claimable once it is past
    that 30-minute window; that window is the documented recovery bound after a
    crash, and a row inside it is left alone so a healthy worker keeps it.

    Args:
        session_factory: the factory of the session the query uses.
        enqueue: the function that sends one run id to the worker.

    Returns:
        The selected run ids, in query order.
    """
    now = func.now()
    statement = (
        select(SimulationRun.id)
        .where(
            or_(
                and_(
                    SimulationRun.status == "queued",
                    SimulationRun.requested_at < now - _QUEUED_STALE_AFTER,
                ),
                and_(
                    SimulationRun.status == "running",
                    or_(
                        SimulationRun.started_at.is_(None),
                        SimulationRun.started_at < now - STALE_AFTER,
                    ),
                ),
            )
        )
        .order_by(SimulationRun.requested_at, SimulationRun.id)
    )
    with session_factory() as session:
        run_ids = list(session.scalars(statement))
    for run_id in run_ids:
        enqueue(run_id)
    return run_ids


def on_worker_ready(sender: object = None, **kwargs: object) -> None:
    """Re-enqueue the runs a crashed worker left behind, never raising at boot.

    A worker that starts after a crash is the cheapest moment to repair the
    queue, because no other component is guaranteed to run. A database failure
    is one log line and the worker still starts: refusing to boot would turn a
    recoverable sweep into an outage.
    """
    try:
        engine = make_engine(get_settings())
    except ValueError:
        logger.warning("worker_ready: incomplete database configuration; skipping stale sweep")
        return
    try:
        factory = session_factory(engine)

        def enqueue(run_id: uuid.UUID) -> None:
            app.send_task(SIMULATE_TASK, args=[str(run_id)], queue=QUEUE_NAME)

        requeued = requeue_stale(factory, enqueue)
        logger.info("worker_ready: re-enqueued %d stale run(s)", len(requeued))
    except SQLAlchemyError:
        logger.warning("worker_ready: cannot reach the database; skipping stale sweep")
    finally:
        engine.dispose()


def connect_worker_ready() -> None:
    """Connect :func:`on_worker_ready` to the signal, once per process.

    ``dispatch_uid`` makes a second call a no-op, so a module reload or a test
    that calls this function cannot register the sweep twice.
    """
    worker_ready.connect(on_worker_ready, dispatch_uid=_WORKER_READY_UID)


connect_worker_ready()
