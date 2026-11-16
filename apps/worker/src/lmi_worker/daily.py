"""The daily cycle: ingest the current season, predict, then simulate the league.

The cycle is three steps with different failure rules, so it is one plain
function that any caller can run in-process: the Celery task ``lmi.daily`` at
06:00 Europe/Madrid, ``python -m lmi_worker daily``, or a test. Every step logs
one line and keeps going: a failed download must not cost the day's forecast,
and a failed forecast must not cost the simulation.

1. Ingest the season in progress with the same plan, fetcher and pause the
   ``ingest`` command uses. The network is the least reliable part, so a failure
   is one log line and the rest of the cycle still runs.
2. Store the live predictions with ``trigger="schedule"``. A match dated ``D``
   is last predicted on ``D-1``, which is why the schedule keeps the last
   pre-kickoff forecast.
3. Open and execute one live simulation of the latest season with a stored
   match, in this process. The daily cycle is already a worker process, so
   queueing the run from inside it would add a message that can be lost.

Run as the Celery task, the cycle is called with ``escalate=True``: a transient
database failure (by :func:`lmi_core.db.errors.is_transient_db_error`) and a
Celery soft time limit then leave the cycle instead of becoming a logged step
failure, so the task can retry the whole idempotent cycle or fail at its soft
limit. A network failure of the ingest step and every deterministic failure stay
contained in both modes.

The exit code follows the other commands: 0 when the prediction and simulation
steps succeeded, 1 when either failed, 2 when the database configuration is
incomplete. The ingest step only contributes a log line. No line ever carries a
database URL, a password or an environment value.

One engine is built for the whole cycle and disposed in a ``finally``. The
collaborators are module-level names looked up at call time, so a test can
replace the database and the network without a live server.
"""

import datetime
import functools
import logging
from collections.abc import Sequence
from typing import Final

import httpx
from celery.exceptions import SoftTimeLimitExceeded
from lmi_core.clock import madrid_today
from lmi_core.config import get_settings
from lmi_core.db.errors import is_transient_db_error
from lmi_core.db.models import Match, Season, SimulationRun
from lmi_core.db.session import make_engine, session_factory
from lmi_core.simulation import DEFAULT_SEED, Qualification
from lmi_core.simulation.service import create_run, execute_run
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from lmi_worker import http
from lmi_worker.ingest.pipeline import Fetcher, run_jobs
from lmi_worker.ingest.plan import plan_jobs
from lmi_worker.predict import store_predictions
from lmi_worker.seasons import current_season
from lmi_worker.simulate import DEFAULT_RUNS

logger = logging.getLogger(__name__)

_PAUSE_SECONDS: Final = 1.0
"""The wait between two downloads, the same pause the ``ingest`` command uses."""

_ERROR_LIMIT: Final = 500
"""The longest failure text one log line may carry."""


def main(argv: Sequence[str] | None = None) -> int:
    """Run one daily cycle for ``python -m lmi_worker daily``.

    ``daily`` takes no flags; the argument is accepted and ignored so the
    command-line entry point can call every command the same way.
    """
    del argv
    return run_daily_cycle()


def run_daily_cycle(
    *,
    today: datetime.date | None = None,
    fetcher: Fetcher | None = None,
    escalate: bool = False,
) -> int:
    """Run the three steps of the daily cycle and return the process exit code.

    Args:
        today: the calendar date that decides the season in progress and every
            as-of date. ``None`` reads the Madrid date, which is what production
            does; a test can pin it.
        fetcher: the download function of the ingest step. ``None`` builds one
            HTTP client for the whole step; a test supplies a fake.
        escalate: re-raise a transient database failure and a soft time limit
            instead of containing them, as the Celery task needs. The command
            line keeps the default and its exit codes.

    Returns:
        0 when the prediction and simulation steps succeeded, 1 when either
        failed or the database cannot be reached, 2 when the database
        configuration is incomplete.
    """
    as_of = today if today is not None else madrid_today()
    try:
        engine = make_engine(get_settings())
    except ValueError as error:
        # The message names the missing environment variables and never a value.
        logger.error("daily: %s", error)
        return 2

    try:
        factory = session_factory(engine)
        _ingest(engine, as_of, fetcher, escalate=escalate)
        predicted = _predict(factory, as_of, escalate=escalate)
        simulated = _simulate(factory, as_of, escalate=escalate)
        return 0 if predicted and simulated else 1
    except SQLAlchemyError as error:
        if _must_escalate(error, escalate=escalate):
            raise
        # The driver text can carry the connection URL, so only the failure kind
        # is logged.
        logger.error("daily: cannot reach the database")
        return 1
    finally:
        engine.dispose()


def _must_escalate(error: BaseException, *, escalate: bool) -> bool:
    """Return whether ``error`` must leave the cycle instead of being contained.

    Only in ``escalate`` mode, and only for a soft time limit or a transient
    database failure; a network failure of the ingest step is not a database
    failure and stays contained.
    """
    if not escalate:
        return False
    if isinstance(error, SoftTimeLimitExceeded):
        return True
    return isinstance(error, DBAPIError) and is_transient_db_error(error)


def _ingest(
    engine: Engine, day: datetime.date, fetcher: Fetcher | None, *, escalate: bool = False
) -> None:
    """Ingest the season in progress; a failure logs one line and returns."""
    try:
        jobs = plan_jobs(season=current_season(day).code, today=day)
        client: httpx.Client | None = None
        try:
            if fetcher is None:
                client = httpx.Client()
                download: Fetcher = functools.partial(http.fetch, client=client)
            else:
                download = fetcher
            results = run_jobs(
                engine,
                jobs,
                download,
                functools.partial(http.polite_pause, _PAUSE_SECONDS),
            )
        finally:
            if client is not None:
                client.close()
    except Exception as error:
        if _must_escalate(error, escalate=escalate):
            raise
        logger.warning("daily ingest: %s", _failure_text(error))
        return

    succeeded = sum(1 for result in results if result.status == "succeeded")
    failed = len(results) - succeeded
    logger.info("daily ingest: %d succeeded, %d failed", succeeded, failed)


def _predict(
    factory: sessionmaker[Session], as_of: datetime.date, *, escalate: bool = False
) -> bool:
    """Store the live predictions of ``as_of`` and report whether it worked."""
    try:
        with factory() as session:
            stored = store_predictions(session, as_of=as_of, trigger="schedule")
    except Exception as error:
        if _must_escalate(error, escalate=escalate):
            raise
        logger.warning("daily predict: %s", _failure_text(error))
        return False
    logger.info(
        "daily predict: run %s inserted=%d existing=%d",
        stored.run_id,
        stored.written.n_inserted,
        stored.written.n_existing,
    )
    return True


def _simulate(
    factory: sessionmaker[Session], as_of: datetime.date, *, escalate: bool = False
) -> bool:
    """Run one live simulation of the latest season and report whether it worked."""
    try:
        with factory() as session:
            season_code = _latest_season(session)
            run, _ = create_run(
                session,
                season_code=season_code,
                as_of_date=as_of,
                n_runs=DEFAULT_RUNS,
                seed=DEFAULT_SEED,
                trigger="schedule",
                qualification=Qualification(),
                kind="live",
            )
    except (ValueError, SQLAlchemyError) as error:
        if _must_escalate(error, escalate=escalate):
            raise
        logger.warning("daily simulate: %s", _failure_text(error))
        return False

    logger.info("daily simulate: run %s for %s as of %s", run.id, season_code, as_of)
    execute_run(factory, run.id)
    with factory() as session:
        session.expire_all()
        stored = session.get(SimulationRun, run.id)
    if stored is None:
        logger.warning("daily simulate: run %s disappeared before it could be read", run.id)
        return False
    if stored.status != "succeeded":
        logger.warning("daily simulate: run %s finished as %s", run.id, stored.status)
        return False
    logger.info("daily simulate: run %s succeeded", run.id)
    return True


def _latest_season(session: Session) -> str:
    """Return the code of the season with a stored match and the greatest start year.

    Raises:
        ValueError: no season has a stored match, so there is nothing to
            simulate.
    """
    code = session.scalar(
        select(Season.code)
        .join(Match, Match.season_id == Season.id)
        .order_by(Season.start_year.desc())
        .limit(1)
    )
    if code is None:
        raise ValueError("no season has a stored match")
    return str(code)


def _failure_text(error: BaseException) -> str:
    """Return one log-safe line for ``error``.

    A :class:`~sqlalchemy.exc.SQLAlchemyError` is reduced to its class name,
    because a driver message can carry the connection URL. Every other error
    keeps ``class name: message``, truncated to :data:`_ERROR_LIMIT`.
    """
    if isinstance(error, SQLAlchemyError):
        return type(error).__name__
    return f"{type(error).__name__}: {error}"[:_ERROR_LIMIT]
