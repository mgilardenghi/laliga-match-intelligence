"""Run bookkeeping and cross-process locking for the ingestion pipeline.

Two locks guard an ingestion, and they answer different questions. The run lock of
:func:`run_lock` is held for the whole life of one job, including the download, so
two processes never load the same ``(source, season)`` at the same time; it is a
session-level advisory lock on a dedicated connection. The season write lock of
:func:`lmi_worker.ingest.store.lock_season_writes` is held for one data
transaction, so the football-data loader and the openfootball loader of the same
season cannot interleave their reads and writes even when they run as separate
jobs.

Every run is recorded twice in two separate transactions. :func:`start_run`
commits the ``running`` row before the file is fetched, so a process that dies
during the download leaves a trace instead of nothing. :func:`finish_run` writes
the terminal status and the diagnostics in its own transaction, so a rollback of
the data transaction cannot erase the reason the run failed.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from lmi_core.db.models import IngestionRejection, IngestionRun
from sqlalchemy import func, insert, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from lmi_worker.ingest.plan import Job
from lmi_worker.ingest.store import LOCK_CLASS
from lmi_worker.sources.football_data import FdRejection

SOURCE_LOCK_IDS: dict[str, int] = {"football_data": 1, "openfootball": 2}
"""The second half of a run lock key: one small number per source."""

ABANDONED_ERROR = "abandoned: the process ended before the run finished"
"""The error stored on a ``running`` row that a later run had to give up on."""


@dataclass(frozen=True)
class RunOutcome:
    """The terminal state of one run, as :func:`finish_run` stores it."""

    status: Literal["succeeded", "failed"]
    rows_read: int = 0
    rows_upserted: int = 0
    rows_removed: int = 0
    rows_rejected: int = 0
    content_sha256: str | None = None
    error: str | None = None
    rejections: tuple[FdRejection, ...] = ()


def lock_key(job: Job) -> int:
    """Return the run lock key of ``job``: the source id and the season start year.

    The key space is ``source_id * 10000 + start_year``, so it stays below the
    season write lock's much larger space and a run lock can never block the data
    transaction of another job.
    """
    return SOURCE_LOCK_IDS[job.source] * 10000 + job.season.start_year


@contextmanager
def run_lock(engine: Engine, job: Job) -> Iterator[bool]:
    """Hold the run lock of ``job`` for the duration of the ``with`` block.

    The lock lives on a dedicated connection taken from ``engine``, because a
    session-level advisory lock ends when its connection closes. A second
    overlapping run of the same ``(source, season)`` therefore gets ``False``
    instead of waiting: the caller skips the job and reports it as locked, which
    is more useful than a process that blocks for the length of a download.

    Yields:
        ``True`` when the lock was acquired, ``False`` when another process holds
        it. The lock is released in ``finally``, also when the block raises.
    """
    key = lock_key(job)
    connection = engine.connect()
    try:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_class, :key)"),
                {"lock_class": LOCK_CLASS, "key": key},
            ).scalar_one()
        )
        connection.commit()
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_class, :key)"),
                    {"lock_class": LOCK_CLASS, "key": key},
                )
                connection.commit()
    finally:
        connection.close()


def start_run(factories: sessionmaker[Session], job: Job, loader_version: str) -> UUID:
    """Record a new ``running`` run for ``job`` in one committed transaction.

    Any ``running`` row of the same ``(source, season)`` belongs to a process that
    ended without finishing, so it is marked failed with
    :data:`ABANDONED_ERROR` before the new row is inserted. The commit returns
    before the caller fetches the file, so an interrupted download still leaves a
    run row behind.
    """
    with factories.begin() as session:
        session.execute(
            update(IngestionRun)
            .where(
                IngestionRun.source == job.source,
                IngestionRun.season_code == job.season.code,
                IngestionRun.status == "running",
            )
            .values(status="failed", finished_at=func.now(), error=ABANDONED_ERROR)
        )
        run_id = session.execute(
            insert(IngestionRun)
            .values(
                source=job.source,
                season_code=job.season.code,
                loader_version=loader_version,
                status="running",
            )
            .returning(IngestionRun.id)
        ).scalar_one()
    return run_id


def finish_run(factories: sessionmaker[Session], run_id: UUID, outcome: RunOutcome) -> None:
    """Store the terminal status, the counters and the diagnostics of one run.

    The terminal status and ``finished_at`` are written by the same statement,
    because the ``finished_matches_status`` check constraint requires the two to
    agree. The transaction is independent of the data transaction, so the error
    and the rejected rows of a rolled-back load still survive.

    Raises:
        RuntimeError: the run row is missing or already terminal elsewhere.
    """
    with factories.begin() as session:
        rows = session.execute(
            update(IngestionRun)
            .where(IngestionRun.id == run_id)
            .values(
                status=outcome.status,
                finished_at=func.now(),
                rows_read=outcome.rows_read,
                rows_upserted=outcome.rows_upserted,
                rows_removed=outcome.rows_removed,
                rows_rejected=outcome.rows_rejected,
                content_sha256=outcome.content_sha256,
                error=outcome.error,
            )
            .returning(IngestionRun.id)
        ).all()
        if len(rows) != 1:
            raise RuntimeError(
                f"update ingestion_runs for run {run_id} affected {len(rows)} rows, expected 1"
            )
        for rejection in outcome.rejections:
            session.execute(
                insert(IngestionRejection).values(
                    run_id=run_id,
                    row_number=rejection.row_number,
                    reason=rejection.reason,
                    raw=_jsonb_raw(rejection.raw),
                )
            )


def _jsonb_raw(raw: Mapping[str, str]) -> dict[str, object]:
    """Return the rejected cells as plain JSON data that JSONB accepts."""
    return {str(key): str(value) for key, value in raw.items()}
