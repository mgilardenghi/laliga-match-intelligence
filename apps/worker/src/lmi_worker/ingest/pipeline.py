"""Run one ingestion job and record its outcome.

The runner owns the run lifecycle. It takes the run lock of a ``(source,
season)`` pair, commits the ``running`` row before the download, applies the
file in one data transaction and writes the terminal status in a separate
transaction. A failure therefore never erases its own diagnostics, and the next
run of the same pair marks a row that no process finished as abandoned.

The pipeline is the only place that combines the pure layers: the parser turns
bytes into a snapshot, the alias resolver maps source names to team ids, the
merge planner compares the snapshot with the stored season and the store applies
the plan. Every one of those steps is outside the write transaction except the
merge, so a file that is rejected as a whole leaves the database untouched.
"""

import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from lmi_core.db.models import Match
from lmi_core.db.seed import normalize_name
from lmi_core.db.session import session_factory
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from lmi_worker.ingest.aliases import AliasResolver, UnknownTeamNames
from lmi_worker.ingest.merge import (
    FdRecord,
    MergePlan,
    OfRecord,
    check_full_season,
    check_unique_keys,
    fd_record,
    of_record,
    plan_football_data,
    plan_openfootball,
)
from lmi_worker.ingest.plan import Job, source_url
from lmi_worker.ingest.runs import RunOutcome, finish_run, run_lock, start_run
from lmi_worker.ingest.store import apply_plan, get_season, load_season, lock_season_writes
from lmi_worker.sources.football_data import FdRejection, FdSnapshot, parse_csv
from lmi_worker.sources.openfootball import OfFixture, parse_json

Fetcher = Callable[[str], bytes]
"""Download one URL and return its body."""

LOADER_VERSION = "1"
"""The version of the loading rules, stored on every run row for the audit trail."""

_ERROR_LIMIT = 4000
"""The maximum number of characters stored in ``ingestion_runs.error``."""

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobResult:
    """The visible outcome of one job.

    ``run_id`` is ``None`` only for a locked job, because a job that never got
    its run lock never starts a run and writes no row at all.
    """

    job: Job
    status: Literal["succeeded", "failed", "locked"]
    run_id: UUID | None
    rows_read: int = 0
    rows_upserted: int = 0
    rows_removed: int = 0
    rows_rejected: int = 0
    error: str | None = None


@dataclass
class _Progress:
    """The counters of the data transaction that committed.

    The runner fills them only after :func:`_load` returned, which it does after
    the transaction committed. A write that was rolled back therefore leaves
    them at zero, so the run row never reports rows it did not store.
    """

    rows_upserted: int = 0
    rows_removed: int = 0


def run_job(engine: Engine, job: Job, fetch: Fetcher) -> JobResult:
    """Run one ingestion job and return its outcome.

    A job whose run lock another process holds returns ``locked`` without
    starting a run. Every other path records a run: ``succeeded`` when the file
    is applied, ``failed`` when the fetch, the parse, the validation or the
    write fails. The data transaction is rolled back on any failure, and the
    terminal status is written afterwards in a transaction of its own.

    Args:
        engine: the database engine the run and the data use.
        job: the source and season to load.
        fetch: the download function, which is never called for a locked job.
    """
    with run_lock(engine, job) as acquired:
        if not acquired:
            return JobResult(job=job, status="locked", run_id=None)

        factories = session_factory(engine)
        run_id = start_run(factories, job, LOADER_VERSION)
        rows_read = 0
        rows_rejected = 0
        rejections: tuple[FdRejection, ...] = ()
        progress = _Progress()
        content_sha256: str | None = None
        snapshot: FdSnapshot | None = None
        fixtures: list[OfFixture] | None = None

        try:
            body = fetch(source_url(job))
            content_sha256 = hashlib.sha256(body).hexdigest()
            if job.source == "football_data":
                snapshot = parse_csv(body)
                rows_read = len(snapshot.matches) + len(snapshot.rejections)
                rows_rejected = len(snapshot.rejections)
                rejections = tuple(snapshot.rejections)
            else:
                fixtures = parse_json(body)
                rows_read = len(fixtures)
            _load(factories, job, snapshot, fixtures, progress)
        except UnknownTeamNames as unknown:
            rows_rejected = _matches_with_unknown_names(snapshot, fixtures, unknown.names)
            error = str(unknown)[:_ERROR_LIMIT]
            _finish(
                factories,
                run_id,
                RunOutcome(
                    status="failed",
                    rows_read=rows_read,
                    rows_upserted=progress.rows_upserted,
                    rows_removed=progress.rows_removed,
                    rows_rejected=rows_rejected,
                    content_sha256=content_sha256,
                    error=error,
                ),
            )
            return JobResult(
                job=job,
                status="failed",
                run_id=run_id,
                rows_read=rows_read,
                rows_upserted=progress.rows_upserted,
                rows_removed=progress.rows_removed,
                rows_rejected=rows_rejected,
                error=error,
            )
        except Exception as error:
            message = f"{type(error).__name__}: {error}"[:_ERROR_LIMIT]
            _finish(
                factories,
                run_id,
                RunOutcome(
                    status="failed",
                    rows_read=rows_read,
                    rows_upserted=progress.rows_upserted,
                    rows_removed=progress.rows_removed,
                    rows_rejected=rows_rejected,
                    content_sha256=content_sha256,
                    error=message,
                    rejections=rejections,
                ),
            )
            return JobResult(
                job=job,
                status="failed",
                run_id=run_id,
                rows_read=rows_read,
                rows_upserted=progress.rows_upserted,
                rows_removed=progress.rows_removed,
                rows_rejected=rows_rejected,
                error=message,
            )

        _finish(
            factories,
            run_id,
            RunOutcome(
                status="succeeded",
                rows_read=rows_read,
                rows_upserted=progress.rows_upserted,
                rows_removed=progress.rows_removed,
                rows_rejected=rows_rejected,
                content_sha256=content_sha256,
            ),
        )
        return JobResult(
            job=job,
            status="succeeded",
            run_id=run_id,
            rows_read=rows_read,
            rows_upserted=progress.rows_upserted,
            rows_removed=progress.rows_removed,
            rows_rejected=rows_rejected,
        )


def run_jobs(
    engine: Engine,
    jobs: Sequence[Job],
    fetch: Fetcher,
    pause: Callable[[], None],
) -> list[JobResult]:
    """Run every job in order and return one result per job.

    ``pause`` is called between two jobs but never after the last one, so a
    command does not wait for a request it will not make. A failed or locked job
    does not stop the remaining jobs.
    """
    results: list[JobResult] = []
    for position, job in enumerate(jobs):
        if position > 0:
            pause()
        results.append(run_job(engine, job, fetch))
    return results


def _load(
    factories: sessionmaker[Session],
    job: Job,
    snapshot: FdSnapshot | None,
    fixtures: list[OfFixture] | None,
    progress: _Progress,
) -> None:
    """Apply the parsed snapshot in one data transaction.

    The transaction takes the season write lock first, so two sources loading
    the same season cannot interleave. A file with parser rejections or unknown
    team names fails before the first write; a duplicate key or an incomplete
    fixture list fails before it too. The caller's session context commits on
    success and rolls back on any exception.

    ``progress`` is filled only after the transaction committed. Until then the
    runner holds no evidence that a row was stored, so a failure anywhere in
    this function leaves the counters at zero.

    Raises:
        ValueError: the snapshot has rejected rows.
        UnknownTeamNames: a source name is absent from the alias table.
        DuplicateKey: two records share a natural key or a team plays itself.
        SeasonShapeError: the fixture list is not a complete league season.
        SourceConflict: the fixture list omits a played match.
        RuntimeError: the stored fixture count contradicts the snapshot after
            the write.
    """
    with factories.begin() as session:
        lock_season_writes(session, job.season.start_year)
        season = get_season(session, job.season.code)
        if job.source == "football_data":
            if snapshot is None:
                raise ValueError("the football-data snapshot is missing")
            plan = _football_data_plan(session, job, season.id, snapshot)
        else:
            if fixtures is None:
                raise ValueError("the openfootball fixture list is missing")
            plan = _openfootball_plan(session, job, season.id, fixtures)
        apply_plan(session, season.id, plan)
        if fixtures is not None:
            _assert_full_season_stored(session, season.id, len(fixtures))
        rows_upserted = plan.rows_upserted
        rows_removed = plan.rows_removed
    progress.rows_upserted = rows_upserted
    progress.rows_removed = rows_removed


def _football_data_plan(
    session: Session, job: Job, season_id: int, snapshot: FdSnapshot
) -> MergePlan:
    """Build the merge plan of one football-data snapshot."""
    if snapshot.rejections:
        first = snapshot.rejections[0]
        raise ValueError(
            f"the file has {len(snapshot.rejections)} rejected row(s); "
            f"the first is row {first.row_number}: {first.reason}"
        )
    resolver = AliasResolver.load(session, job.source)
    names = [match.home for match in snapshot.matches]
    names.extend(match.away for match in snapshot.matches)
    resolved = resolver.resolve(names)
    records: list[FdRecord] = [
        fd_record(match, resolved[match.home], resolved[match.away]) for match in snapshot.matches
    ]
    check_unique_keys(records)
    return plan_football_data(load_season(session, season_id), records)


def _openfootball_plan(
    session: Session, job: Job, season_id: int, fixtures: list[OfFixture]
) -> MergePlan:
    """Build the merge plan of one openfootball fixture list."""
    resolver = AliasResolver.load(session, job.source)
    names = [fixture.team1 for fixture in fixtures]
    names.extend(fixture.team2 for fixture in fixtures)
    resolved = resolver.resolve(names)
    records: list[OfRecord] = [
        of_record(fixture, resolved[fixture.team1], resolved[fixture.team2]) for fixture in fixtures
    ]
    check_unique_keys(records)
    check_full_season(records)
    return plan_openfootball(load_season(session, season_id), records)


def _assert_full_season_stored(session: Session, season_id: int, expected: int) -> None:
    """Fail unless the season stores exactly the fixtures of the snapshot."""
    stored = session.scalar(
        select(func.count()).select_from(Match).where(Match.season_id == season_id)
    )
    if int(stored or 0) != expected:
        raise RuntimeError(
            f"the season stores {stored or 0} matches after the openfootball load, "
            f"expected the {expected} fixtures of the snapshot"
        )


def _matches_with_unknown_names(
    snapshot: FdSnapshot | None,
    fixtures: list[OfFixture] | None,
    names: Sequence[str],
) -> int:
    """Count the matches of the snapshot that reference an unknown team name.

    The counter is the number of matches a reader must look at, not the number
    of names: one unknown spelling can appear in several fixtures.
    """
    unknown = set(names)
    if snapshot is not None:
        return sum(
            1
            for match in snapshot.matches
            if normalize_name(match.home) in unknown or normalize_name(match.away) in unknown
        )
    if fixtures is not None:
        return sum(
            1
            for fixture in fixtures
            if normalize_name(fixture.team1) in unknown or normalize_name(fixture.team2) in unknown
        )
    return 0


def _finish(factories: sessionmaker[Session], run_id: UUID, outcome: RunOutcome) -> None:
    """Write the terminal status, logging a failure instead of raising it.

    A terminal write that fails leaves the ``running`` row behind; the next run
    of the same source and season marks it abandoned, so the audit trail still
    ends cleanly.
    """
    try:
        finish_run(factories, run_id, outcome)
    except Exception:
        _logger.exception(
            "could not record the terminal status of run %s; the next run marks it abandoned",
            run_id,
        )
