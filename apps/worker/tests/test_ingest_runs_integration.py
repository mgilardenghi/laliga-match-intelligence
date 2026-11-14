"""Integration tests for the run lifecycle and the run lock.

The run bookkeeping is the audit trail of the ingestion, so these tests check the
two properties the design relies on: the ``running`` row is committed before the
file is fetched, and the terminal status is written in a transaction of its own so
a rolled-back load still reports why it failed. A ``running`` row that no process
owns any more is marked abandoned by the next run of the same source and season,
and a run of a different season leaves it alone.

The run lock is tested the way it is used: held for one job, probed from another
connection and from nested blocks. A second overlapping run must get ``False``
instead of waiting, and a different source or season must not block at all.

These tests need a live PostgreSQL and are opt-in through ``-m integration``; the
``migrated_engine`` fixture refuses a database that is not explicitly disposable
and fails instead of skipping when the ``POSTGRES_*`` variables are unset.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from lmi_core.db.models import IngestionRejection, IngestionRun
from lmi_worker.ingest.plan import Job
from lmi_worker.ingest.runs import (
    ABANDONED_ERROR,
    SOURCE_LOCK_IDS,
    RunOutcome,
    finish_run,
    lock_key,
    run_lock,
    start_run,
)
from lmi_worker.seasons import SeasonCode
from lmi_worker.sources.football_data import FdRejection
from sqlalchemy import select

if TYPE_CHECKING:
    from conftest import MigratedDatabase

pytestmark = pytest.mark.integration

SEASON = SeasonCode.parse("2026-27")
PREVIOUS_SEASON = SeasonCode.parse("2025-26")

FD_JOB = Job(source="football_data", season=SEASON)
OF_JOB = Job(source="openfootball", season=SEASON)
PREVIOUS_FD_JOB = Job(source="football_data", season=PREVIOUS_SEASON)

LOADER_VERSION = "test"


def _runs(migrated: MigratedDatabase) -> dict[UUID, IngestionRun]:
    """Return every stored run, keyed by its id, read in a fresh session."""
    with migrated.factories() as session:
        return {run.id: run for run in session.scalars(select(IngestionRun))}


def _rejections(migrated: MigratedDatabase, run_id: UUID) -> list[IngestionRejection]:
    """Return the rejections of one run, ordered by row number."""
    with migrated.factories() as session:
        return list(
            session.scalars(
                select(IngestionRejection)
                .where(IngestionRejection.run_id == run_id)
                .order_by(IngestionRejection.row_number)
            )
        )


def test_start_run_commits_a_running_row(migrated_engine: MigratedDatabase) -> None:
    run_id = start_run(migrated_engine.factories, FD_JOB, LOADER_VERSION)

    runs = _runs(migrated_engine)
    assert list(runs) == [run_id]
    run = runs[run_id]
    assert run.source == "football_data"
    assert run.season_code == "2026-27"
    assert run.loader_version == LOADER_VERSION
    assert run.status == "running"
    assert run.finished_at is None
    assert run.started_at is not None
    assert (run.rows_read, run.rows_upserted, run.rows_removed, run.rows_rejected) == (0, 0, 0, 0)


def test_a_second_start_run_marks_the_first_row_abandoned(
    migrated_engine: MigratedDatabase,
) -> None:
    first = start_run(migrated_engine.factories, FD_JOB, LOADER_VERSION)
    second = start_run(migrated_engine.factories, FD_JOB, LOADER_VERSION)

    runs = _runs(migrated_engine)
    assert len(runs) == 2
    assert runs[first].status == "failed"
    assert runs[first].finished_at is not None
    assert runs[first].error == ABANDONED_ERROR
    assert runs[second].status == "running"
    assert runs[second].finished_at is None
    assert runs[second].error is None


def test_start_run_of_another_season_leaves_the_running_row_alone(
    migrated_engine: MigratedDatabase,
) -> None:
    current = start_run(migrated_engine.factories, FD_JOB, LOADER_VERSION)
    previous = start_run(migrated_engine.factories, PREVIOUS_FD_JOB, LOADER_VERSION)

    runs = _runs(migrated_engine)
    assert len(runs) == 2
    assert runs[current].status == "running"
    assert runs[current].finished_at is None
    assert runs[current].error is None
    assert runs[previous].status == "running"
    assert runs[previous].finished_at is None


def test_finish_run_stores_the_succeeded_outcome(migrated_engine: MigratedDatabase) -> None:
    run_id = start_run(migrated_engine.factories, FD_JOB, LOADER_VERSION)
    sha256 = "a" * 64

    finish_run(
        migrated_engine.factories,
        run_id,
        RunOutcome(
            status="succeeded",
            rows_read=380,
            rows_upserted=30,
            rows_removed=4,
            rows_rejected=0,
            content_sha256=sha256,
        ),
    )

    run = _runs(migrated_engine)[run_id]
    assert run.status == "succeeded"
    assert run.finished_at is not None
    assert (run.rows_read, run.rows_upserted, run.rows_removed, run.rows_rejected) == (
        380,
        30,
        4,
        0,
    )
    assert run.content_sha256 == sha256
    assert run.error is None
    assert _rejections(migrated_engine, run_id) == []


def test_finish_run_stores_the_failure_and_one_rejection_row_per_rejection(
    migrated_engine: MigratedDatabase,
) -> None:
    run_id = start_run(migrated_engine.factories, FD_JOB, LOADER_VERSION)
    rejections = (
        FdRejection(
            row_number=3,
            reason="unknown full-time result 'X'",
            raw={"Div": "SP1", "FTR": "X"},
        ),
        FdRejection(
            row_number=7,
            reason="home and away team are the same",
            raw={"HomeTeam": "Alpha FC", "AwayTeam": "Alpha FC"},
        ),
    )

    finish_run(
        migrated_engine.factories,
        run_id,
        RunOutcome(
            status="failed",
            rows_read=2,
            rows_rejected=2,
            error="FdSnapshotError: two rows are unusable",
            rejections=rejections,
        ),
    )

    run = _runs(migrated_engine)[run_id]
    assert run.status == "failed"
    assert run.finished_at is not None
    assert run.rows_read == 2
    assert run.rows_rejected == 2
    assert run.error == "FdSnapshotError: two rows are unusable"
    stored = _rejections(migrated_engine, run_id)
    assert [(row.row_number, row.reason, row.raw) for row in stored] == [
        (3, "unknown full-time result 'X'", {"Div": "SP1", "FTR": "X"}),
        (7, "home and away team are the same", {"HomeTeam": "Alpha FC", "AwayTeam": "Alpha FC"}),
    ]


def test_run_lock_refuses_an_overlapping_run_only(migrated_engine: MigratedDatabase) -> None:
    assert lock_key(FD_JOB) == SOURCE_LOCK_IDS["football_data"] * 10000 + SEASON.start_year

    with run_lock(migrated_engine.engine, FD_JOB) as acquired:
        assert acquired is True
        with run_lock(migrated_engine.engine, FD_JOB) as overlapping:
            assert overlapping is False
        with run_lock(migrated_engine.engine, OF_JOB) as other_source:
            assert other_source is True
        with run_lock(migrated_engine.engine, PREVIOUS_FD_JOB) as other_season:
            assert other_season is True

    with run_lock(migrated_engine.engine, FD_JOB) as released:
        assert released is True
