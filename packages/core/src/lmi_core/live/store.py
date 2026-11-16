"""Append-only persistence for live predictions.

The worker writes one as-of date in three steps: :func:`start_run` inserts a
``running`` prediction run and commits it, :func:`write_batch` stores the
prediction rows and moves the run to ``succeeded`` in one transaction, or
:func:`fail_run` marks the run ``failed`` with the reason. The API and the
scorer read the stored rows back through
:func:`lmi_core.live.data.load_prediction_rows`.

A stored prediction is immutable, and the database is the last authority on
leakage: the ``predictions_guard_insert`` trigger rejects a match that is no
longer scheduled, an ``as_of_date`` that is not the Madrid creation date and a
row timestamped at or after the kickoff date, whatever this module believes.
This module never massages the dates to make a row fit. A rejected insert rolls
the whole transaction back, so the run stays ``running`` and the caller decides
to :func:`fail_run` it with the database's error text.

Sessions are synchronous SQLAlchemy 2 sessions (psycopg 3). Every public
function commits its own transaction and leaves the session usable.

The transition out of ``running`` is serialised with ``SELECT ... FOR UPDATE``
on the run row inside the transaction that writes it, so two sessions can never
both finish the same run: the second caller waits for the first commit and then
sees the committed terminal state. The lock lives only as long as the store's
own transaction, which is rolled back on any failure after the lock is taken.
"""

import contextlib
import datetime
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lmi_core.db.models import Prediction, PredictionRun
from lmi_core.live.predict import PredictionBatch
from lmi_core.results.store import installed_code_version


@dataclass(frozen=True)
class WriteResult:
    """What one :func:`write_batch` call added and what it found already stored.

    ``n_existing`` counts the rows the natural key already held, so the two
    counts always add up to the number of rows of the batch.
    """

    n_inserted: int
    n_existing: int
    n_no_forecast: int


def start_run(
    session: Session,
    *,
    as_of: datetime.date,
    horizon_days: int,
    trigger: Literal["cli", "schedule"] = "cli",
    code_version: str | None = None,
) -> uuid.UUID:
    """Insert a ``running`` prediction run and commit it.

    Args:
        session: the session that writes the row.
        as_of: the Madrid date the batch is produced for.
        horizon_days: how many days past ``as_of`` the batch reaches. The
            column accepts ``1..60``.
        trigger: what started the run.
        code_version: the version to record. ``None`` reads the installed
            package version through
            :func:`lmi_core.results.store.installed_code_version`.

    Returns:
        The id the database generated for the run.
    """
    run = PredictionRun(
        status="running",
        as_of_date=as_of,
        trigger=trigger,
        horizon_days=horizon_days,
        code_version=installed_code_version() if code_version is None else code_version,
    )
    session.add(run)
    # The flush assigns the generated id and any constraint violation
    # (``horizon_days = 0``, for example) belongs inside the rollback-protected
    # block, so a rejected start leaves the session usable.
    with _transaction(session):
        session.flush()
        run_id = run.id
    return run_id


def write_batch(session: Session, run_id: uuid.UUID, batch: PredictionBatch) -> WriteResult:
    """Store the rows of ``batch`` and finish the run ``succeeded``.

    Everything happens in one transaction: the run row is locked, every row of
    the batch is inserted with ``ON CONFLICT DO NOTHING`` on the natural key
    ``(match_id, model, as_of_date)``, and the run row records the coverage,
    the fingerprint and the transition to ``succeeded``. A reader therefore
    never sees a succeeded run whose prediction set is partial.

    ``as_of_date`` comes from the batch and ``created_at`` from the database:
    the insert trigger stamps it, and sending a value would only be overwritten.
    A batch with no rows skips the insert statement entirely.

    Before the write transaction opens, the session's current transaction is
    committed. PostgreSQL defines ``now()`` as the instant the *transaction*
    started, and the caller's SELECTs (the builder reads history and fixtures)
    opened that transaction before this call; without the commit the trigger
    would stamp a row built across Madrid midnight with the earlier instant.
    The limit is honest: a single statement that itself straddles midnight can
    still be stamped with the instant it began, and the full fix needs a forward
    migration on ``0003``, which this module may not add.

    Args:
        session: the session that writes the rows.
        run_id: the running run the rows belong to.
        batch: the rows and the coverage of one as-of date.

    Returns:
        The insert and existing counts of this call.

    Raises:
        ValueError: the run does not exist or is no longer ``running``, so
            nothing was written. Any database rejection of a row (a match that
            is not scheduled, an ``as_of_date`` that is not the Madrid date
            today, a row stamped at or after the kickoff date) propagates after
            the transaction was rolled back, leaving the run ``running`` for the
            caller to fail.
    """
    # End the read transaction the caller's SELECTs opened, so the write below
    # runs in its own and the insert trigger's ``now()`` is the instant of the
    # insert rather than the instant the reads began.
    session.commit()

    with _transaction(session):
        run = _running_run(session, run_id)
        n_inserted = _insert_rows(session, run_id, batch)
        n_existing = len(batch.rows) - n_inserted
        n_no_forecast = sum(count.n_no_forecast for count in batch.counts)

        run.n_history = batch.n_history
        run.n_fixtures = batch.n_fixtures
        run.data_fingerprint = batch.data_fingerprint
        run.n_inserted = n_inserted
        run.n_existing = n_existing
        run.n_no_forecast = n_no_forecast
        run.status = "succeeded"
        run.finished_at = datetime.datetime.now(datetime.UTC)

    return WriteResult(
        n_inserted=n_inserted,
        n_existing=n_existing,
        n_no_forecast=n_no_forecast,
    )


def fail_run(session: Session, run_id: uuid.UUID, *, error: str) -> None:
    """Mark a running run ``failed`` and record why.

    Args:
        session: the session that writes the row.
        run_id: the running run to fail.
        error: the text stored in ``error``, normally the message of the
            exception that stopped the batch.

    Raises:
        ValueError: the run does not exist or is no longer ``running``. The row
            is left untouched in both cases.
    """
    with _transaction(session):
        run = _running_run(session, run_id)
        run.status = "failed"
        run.finished_at = datetime.datetime.now(datetime.UTC)
        run.error = error


def _insert_rows(session: Session, run_id: uuid.UUID, batch: PredictionBatch) -> int:
    """Insert every row of the batch and return how many the table accepted.

    A natural-key conflict is not an error: the row of that ``(match_id, model,
    as_of_date)`` already exists and stays untouched, because a stored
    prediction is immutable. The rows the statement actually inserted are the
    ones it returns.
    """
    if not batch.rows:
        return 0
    statement = (
        insert(Prediction)
        .values(
            [
                {
                    "run_id": run_id,
                    "match_id": row.match_id,
                    "model": row.model,
                    "model_version": row.model_version,
                    "model_config": row.model_config,
                    "as_of_date": batch.as_of,
                    "p_home": row.p_home,
                    "p_draw": row.p_draw,
                    "p_away": row.p_away,
                    "odds_source": row.odds_source,
                }
                for row in batch.rows
            ]
        )
        .on_conflict_do_nothing(index_elements=["match_id", "model", "as_of_date"])
        .returning(Prediction.id)
    )
    return len(session.execute(statement).scalars().all())


@contextlib.contextmanager
def _transaction(session: Session) -> Iterator[None]:
    """Commit the block, or roll the whole transaction back before re-raising.

    Every writer of this module runs inside this block, so a rejected write
    never leaves work staged, a row lock held or the session unusable: the
    rollback releases the ``SELECT ... FOR UPDATE`` lock of
    :func:`_running_run` as well.
    """
    try:
        yield
        session.commit()
    except Exception:
        session.rollback()
        raise


def _running_run(session: Session, run_id: uuid.UUID) -> PredictionRun:
    """Lock the run row and return it if it is still running.

    The row is read with ``SELECT ... FOR UPDATE`` inside the caller's
    transaction, and ``populate_existing`` refreshes the mapped attributes from
    that row, so a caller that observed a stale ``running`` object cannot
    overwrite a terminal state another session committed.

    Raises:
        ValueError: the run does not exist or has already finished. The row is
            left untouched in both cases.
    """
    statement = (
        select(PredictionRun)
        .where(PredictionRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    run = session.scalars(statement).one_or_none()
    if run is None:
        raise ValueError(f"prediction run {run_id} does not exist")
    if run.status != "running":
        raise ValueError(f"prediction run {run_id} is {run.status!r}, not 'running'")
    return run
