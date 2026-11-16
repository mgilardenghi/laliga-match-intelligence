"""Producer side of the simulation job queue.

The API hands a committed simulation run to the worker through this module and
nothing else. The message carries only the run id, so the broker never holds an
input the database does not also hold, and a lost message leaves a row that the
worker's stale sweep can pick up again. That is also why no result backend is
configured: the outcome is the run row, which the API reads back from
PostgreSQL instead of asking Celery for a result.

Importing this module touches neither Redis nor the database. The Celery
application is built lazily by :func:`get_queue`, when a route first asks for a
producer, so the API can start before the broker does. The module never imports
``lmi_worker``.
"""

import functools
import uuid
from typing import Annotated, Final, Protocol

from celery import Celery
from fastapi import Depends
from lmi_core.config import get_settings

QUEUE_NAME: Final = "lmi"
"""The queue the worker consumes; ``lmi.simulate`` is routed here by name."""

_TASK_NAME: Final = "lmi.simulate"
"""The task name the worker registers for one simulation run."""


class SimulationQueue(Protocol):
    """The narrow producer interface a route depends on.

    A fake with the same method is enough for a contract test, so the test
    suite never needs a running broker.
    """

    def enqueue(self, run_id: uuid.UUID) -> None:
        """Hand one committed run to the worker."""
        ...


class CelerySimulationQueue:
    """Send run ids to the worker through the Celery broker.

    The application is broker-only: no result backend, and every task ignores
    its result. Sending a task is lazy, so constructing this object does not
    open a connection either.
    """

    def __init__(self, broker_url: str) -> None:
        self._app = Celery("lmi-api", broker=broker_url)
        self._app.conf.task_ignore_result = True

    def enqueue(self, run_id: uuid.UUID) -> None:
        """Send ``run_id`` to the worker's queue."""
        self._app.send_task(_TASK_NAME, args=[str(run_id)], queue=QUEUE_NAME)


@functools.cache
def get_queue() -> SimulationQueue:
    """Return the process-wide producer, built on first use.

    ``functools.cache`` keeps one Celery application per process instead of a
    new connection pool per request. The broker URL comes from the shared
    settings, which are read on the first request rather than at import time.
    """
    return CelerySimulationQueue(get_settings().redis_url)


QueueDep = Annotated[SimulationQueue, Depends(get_queue)]
"""The only way a route obtains a producer.

Contract tests override this dependency with a fake queue, so they need no
Redis and no worker.
"""
