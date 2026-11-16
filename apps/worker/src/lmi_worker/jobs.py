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

The two tasks run to completion under time limits sized from the measured cost
of their work, not from the stale window. On the reference stack one daily cycle
costs about 29 seconds and the largest legal simulation request costs about 65
seconds (50 000 runs; the API accepts 1 000 to 50 000 and the sampler is linear
in the run count), while the default 10 000 runs cost 15.9 seconds.
``lmi.simulate`` therefore gets a 240-second soft limit and a 300-second hard
limit, about 3.7 and 4.6 times the worst legal request, and ``lmi.daily`` gets
420 and 600 seconds, about 14 and 20 times the measured cycle. The hard limit
must stay under the claim's 30-minute
:data:`~lmi_core.simulation.service.STALE_AFTER` window: a longer one could kill
a worker after another executor had already taken the run over, and the same run
would execute twice. 300 seconds is a sixth of that window and 600 seconds a
third, so a run the hard limit kills is never still executing when the stale
branch becomes available.

A soft-limit hit raises :class:`celery.exceptions.SoftTimeLimitExceeded`, which
is an ``Exception``. :func:`~lmi_core.simulation.service.execute_run` catches
every ``Exception`` from the claimed work, rolls it back and records the row
``failed``, so the run is terminal and never left ``running``. This module does
not catch or convert that exception; there is no ``try`` around ``execute_run``.
The daily task runs its simulation in-process through the same function, so the
same guarantee holds there, and the cycle reports the failed run in its own log
line and exit code. A daily soft limit that fires during the ingest or
prediction step leaves the cycle too, because the task runs it with
``escalate=True`` (see below). A soft limit raised inside the claim or the
failure bookkeeping can still leave a run ``running`` until the stale window
recovers it.

The recorded text comes from the exception rather than from this module: the
timeout handler raises it with no message, so ``execute_run`` stores what
``str`` of an argument-less exception gives (``SoftTimeLimitExceeded()``), and
the column names the cause without this file inventing a message.

Both tasks retry transient infrastructure failures only, bounded to two retries
with an exponential, jittered backoff capped at 60 seconds. A failure among
:data:`TRANSIENT_ERRORS` is classified by
:func:`lmi_core.db.errors.is_transient_db_error`, which reads the PostgreSQL
SQLSTATE, or the libpq message for a connect-time failure that has none: a
lost or refused connection, a server shutdown or restart, too many connections,
a deadlock or a serialization failure is re-raised as
:class:`TransientJobError`, the only class in ``autoretry_for``; an
authentication failure, an unknown database or any statement error keeps its
own class and is not retried. Both tasks are idempotent. The claim moves a
row to ``running`` only when it is ``queued`` or stale, so a second delivery of
the same message matches no row and returns. The daily cycle's three steps are
idempotent too: the ingest upserts, a stored prediction is immutable and a repeat
batch only counts ``existing``, and a repeat simulation dedupes on the natural
key. A retry is a new message, not a redelivery: ``task_acks_late`` keeps the
message when the process dies, but Celery's retry re-publishes and acknowledges
the old delivery.

What a retry covers is narrow by construction. ``execute_run`` catches every
``Exception`` from the claimed work itself, so the only failures that reach the
task are those raised outside that guard: chiefly the claim's own connection
failure (``_claim`` is not guarded, so a database that is refusing connections
raises ``OperationalError`` out of ``execute_run``) and the engine setup. In both
cases the row is still ``queued``, so the retry does real work. A transient
failure after the claim never reaches the task at all: the row is already
``failed``, or still ``running`` when the bookkeeping write failed too, and the
retry then matches no row and returns without doing anything. The retry cannot
re-claim a row the failed attempt finished; the sweep, or the next worker start,
recovers a row left ``running`` after
:data:`~lmi_core.simulation.service.STALE_AFTER`. A deterministic failure is
never retried: a time-limit hit, a model fit failure (``ModelFitError``) and a
data or configuration error (``ValueError``) are not in the tuple, and the tuple
deliberately does not use ``SQLAlchemyError``, which would also cover
deterministic integrity and programming errors. The daily cycle's own steps
contain their failures deliberately, because a failed download must not cost the
day's forecast. The task runs the cycle with ``escalate=True``, so the two
failures that containment must not hide leave the cycle instead: a transient
database failure (the whole idempotent cycle is then retried) and a soft time
limit (the task then fails instead of running on to the hard limit). A network
failure of the ingest step and a deterministic failure stay contained as a
logged step failure and an exit code, as before.

Broker publishes from this module are bounded too. The sweep sends through
``app.send_task`` under :data:`PUBLISH_RETRY_POLICY`: three attempts with at most
one second between them, so a Redis blip cannot turn one stale row into an
unbounded hang, while a message that is still lost is repaired by the next sweep.
The API has its own Celery application and its own bounded default policy, which
this file does not reach.

Importing this module builds the Celery application but opens no broker
connection: Celery connects lazily when a task is sent or consumed.
"""

import logging
import uuid
from collections.abc import Callable
from typing import Final

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready
from lmi_core.config import get_settings
from lmi_core.db.errors import is_transient_db_error, sqlstate_of
from lmi_core.db.models import SimulationRun
from lmi_core.db.session import make_engine, session_factory
from lmi_core.simulation.service import QUEUED_STALE_AFTER, STALE_AFTER, execute_run
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from lmi_worker import daily as daily_cycle

logger = logging.getLogger(__name__)

QUEUE_NAME: Final = "lmi"
"""The one queue the worker consumes; the API sends ``lmi.simulate`` here."""

SIMULATE_TASK: Final = "lmi.simulate"
DAILY_TASK: Final = "lmi.daily"

SIMULATE_SOFT_TIME_LIMIT: Final = 240
"""Soft time limit of ``lmi.simulate`` in seconds.

The reference stack executes a live simulation of the latest season with the
default 10 000 runs in 15.9 seconds wall clock, and the sampler is linear in
``n_runs``, so the largest legal request (about 65 seconds) is the worst case.
240 seconds is about 3.7 times that worst legal request.
"""

SIMULATE_TIME_LIMIT: Final = 300
"""Hard time limit of ``lmi.simulate`` in seconds.

300 seconds is about 4.6 times the worst legal request and a sixth of the
claim's 30-minute :data:`~lmi_core.simulation.service.STALE_AFTER` window, so a
run the hard limit kills is never still executing when the stale branch becomes
available.
"""

DAILY_SOFT_TIME_LIMIT: Final = 420
"""Soft time limit of ``lmi.daily`` in seconds.

The reference stack runs one daily cycle in about 29 seconds (6.6 seconds of
ingest, 6.4 of live prediction and 15.9 of simulation). 420 seconds is about 14
times that measurement, which leaves room for the ingest step's network pauses
on a slow day.
"""

DAILY_TIME_LIMIT: Final = 600
"""Hard time limit of ``lmi.daily`` in seconds.

600 seconds is about 20 times the measured daily cycle and a third of the claim's
30-minute :data:`~lmi_core.simulation.service.STALE_AFTER` window, so the hard
limit cannot kill a worker after another executor has taken a run over.
"""

TRANSIENT_ERRORS: Final = (OperationalError, InterfaceError, ConnectionError)
"""The infrastructure failures both tasks retry.

``OperationalError`` is a database connection lost or refused, ``InterfaceError``
is a broken database interface, and ``ConnectionError`` is a socket-level
connection failure. ``OperationalError`` also carries deterministic failures such
as an authentication error, which this file cannot separate from a lost
connection; the module docstring states the cost of that. ``SQLAlchemyError`` is
deliberately not used: it would also cover deterministic integrity and
programming errors that a retry cannot fix.
"""

PUBLISH_RETRY_POLICY: Final = {
    "max_retries": 3,
    "interval_start": 0.0,
    "interval_step": 0.2,
    "interval_max": 1.0,
}
"""Bound on the broker publish retries of the messages this application sends.

The application sends only the sweep's messages, so this bounds the retries of a
stale row's publish: three attempts with at most one second between them. A
Redis blip cannot turn one stale row into an unbounded hang, and a message that
is still lost is repaired by the next sweep. The API has its own Celery
application and its own bounded default policy, which this file does not reach.
"""

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
    # Bound the broker's publish retries for the messages this application sends.
    task_publish_retry=True,
    task_publish_retry_policy=PUBLISH_RETRY_POLICY,
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
    except TRANSIENT_ERRORS as error:
        _raise_retryable_if_transient(error)
        raise
    finally:
        engine.dispose()


def run_daily_task() -> None:
    """Run the daily cycle in this worker process and log its exit code.

    The cycle runs with ``escalate=True``, so a transient database failure and a
    soft time limit leave the cycle and reach Celery instead of becoming a
    logged step failure; see :func:`lmi_worker.daily.run_daily_cycle`.
    """
    try:
        exit_code = daily_cycle.run_daily_cycle(escalate=True)
    except TRANSIENT_ERRORS as error:
        _raise_retryable_if_transient(error)
        raise
    logger.info("daily: cycle finished with exit code %d", exit_code)


class TransientJobError(RuntimeError):
    """A transient infrastructure failure; the only error the tasks retry.

    The message names the original class and SQLSTATE only, never the driver
    text, which can carry the connection URL. The original is the ``__cause__``.
    """


def _raise_retryable_if_transient(error: BaseException) -> None:
    """Raise :class:`TransientJobError` from ``error`` when a retry could outlive it.

    A deterministic failure (an authentication error, an unknown database) is
    left alone, so the caller re-raises it unchanged and Celery does not retry.
    """
    if is_transient_db_error(error):
        code = sqlstate_of(error)
        detail = type(error).__name__ if code is None else f"{type(error).__name__} {code}"
        raise TransientJobError(detail) from error


# Registration without the decorator: Celery ships no type information, and
# this form keeps the task functions plain module-level functions. Both tasks run
# under a measured time limit and retry only :class:`TransientJobError`, which
# the task functions raise for a transient failure among :data:`TRANSIENT_ERRORS`.
simulate = app.task(
    name=SIMULATE_TASK,
    soft_time_limit=SIMULATE_SOFT_TIME_LIMIT,
    time_limit=SIMULATE_TIME_LIMIT,
    autoretry_for=(TransientJobError,),
    max_retries=2,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
)(run_simulation_task)
daily = app.task(
    name=DAILY_TASK,
    soft_time_limit=DAILY_SOFT_TIME_LIMIT,
    time_limit=DAILY_TIME_LIMIT,
    autoretry_for=(TransientJobError,),
    max_retries=2,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
)(run_daily_task)


def requeue_stale(
    session_factory: sessionmaker[Session],
    enqueue: Callable[[uuid.UUID], None],
) -> list[uuid.UUID]:
    """Send every abandoned active run to ``enqueue`` and return the ids sent.

    One read-only query selects two kinds of row: a ``queued`` run whose
    ``requested_at`` is older than :data:`QUEUED_STALE_AFTER` (one minute), and a
    ``running`` run with no ``started_at`` or one older than the claim's own
    :data:`STALE_AFTER` window (30 minutes). Both cutoffs are compared with
    ``now()`` in PostgreSQL, never with the process clock, so the decision cannot
    disagree with the row the claim reads. The order is ``requested_at`` then id,
    which makes the sweep deterministic even when two rows share a timestamp.

    Nothing is written. Enqueueing the message is enough, because the claim's
    stale branch in :func:`~lmi_core.simulation.service.execute_run` takes a
    selected row over. A ``running`` row only becomes claimable once it is past
    that 30-minute window; that window is the documented recovery bound after a
    crash, and a row inside it is left alone so a healthy worker keeps it.

    A broker failure for one row is one warning and the sweep continues with the
    next row, so a single refused message cannot strand every stale row behind
    it. Only the ids actually handed to ``enqueue`` are returned; a failed send
    is absent from the result and from the log line the caller reports.

    A refused send leaves the row ``queued`` and writes nothing: ``requested_at``
    stays as it was. The next sweep sends it only once ``requested_at`` is older
    than :data:`QUEUED_STALE_AFTER` (one minute). Because the sweep only runs on
    ``worker_ready``, the real delay is the next worker start, at least one
    minute after the request.

    Args:
        session_factory: the factory of the session the query uses.
        enqueue: the function that sends one run id to the worker.

    Returns:
        The run ids actually handed to the broker, in query order.
    """
    now = func.now()
    statement = (
        select(SimulationRun.id)
        .where(
            or_(
                and_(
                    SimulationRun.status == "queued",
                    SimulationRun.requested_at < now - QUEUED_STALE_AFTER,
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
    enqueued: list[uuid.UUID] = []
    for run_id in run_ids:
        try:
            enqueue(run_id)
        except Exception:
            logger.warning(
                "requeue: cannot enqueue stale run %s; continuing", run_id, exc_info=True
            )
            continue
        enqueued.append(run_id)
    return enqueued


def on_worker_ready(sender: object = None, **kwargs: object) -> None:
    """Re-enqueue the runs a crashed worker left behind, never raising at boot.

    A worker that starts after a crash is the cheapest moment to repair the
    queue, because no other component is guaranteed to run. A database failure
    is one log line and the worker still starts: refusing to boot would turn a
    recoverable sweep into an outage. A broker failure is one log line too: the
    sweep continues past a refused message, and anything it cannot send is
    retried by the next worker that starts.
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
    except Exception:
        logger.warning("worker_ready: the stale sweep failed; skipping it", exc_info=True)
    finally:
        engine.dispose()


def connect_worker_ready() -> None:
    """Connect :func:`on_worker_ready` to the signal, once per process.

    ``dispatch_uid`` makes a second call a no-op, so a module reload or a test
    that calls this function cannot register the sweep twice.
    """
    worker_ready.connect(on_worker_ready, dispatch_uid=_WORKER_READY_UID)


connect_worker_ready()
