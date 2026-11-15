"""Synthetic ingestion runs for the ingestion-run endpoint tests.

Everything inserted here is invented: the counters, the loader version, the
checksums and the rejected rows are made up, nothing is downloaded or ingested,
and no source is contacted. The season codes default to 2098-99, a range no data
source covers, so a run stays clear of the rows the real ingestion left in the
shared database and a test can assert on its own runs only.

Every helper inserts through the ``Session`` it is given and flushes, never
commits: the caller's transaction is the only thing that decides whether the
rows survive, and the flush is what fills the generated identifiers the test
needs to assert on.
"""

import datetime

from lmi_core.db.models import IngestionRejection, IngestionRun
from sqlalchemy.orm import Session


def create_ingestion_run(
    session: Session,
    *,
    started_at: datetime.datetime,
    source: str = "football_data",
    season_code: str = "2098-99",
    loader_version: str = "1.0",
    status: str = "succeeded",
    finished_at: datetime.datetime | None = None,
    rows_read: int = 0,
    rows_upserted: int = 0,
    rows_removed: int = 0,
    rows_withdrawn: int = 0,
    rows_rejected: int = 0,
    content_sha256: str | None = None,
    error: str | None = None,
) -> IngestionRun:
    """Insert one run and return the ORM row with its generated identifier.

    ``started_at`` is required instead of defaulted, because a test that asserts
    on the newest-first order has to write the instants it expects to read back.

    ``finished_at`` follows from ``status``, because the schema ties the two
    with the ``finished_matches_status`` check: a terminal run finished at
    ``started_at`` unless the caller passes the instant it finished, and a
    ``running`` run carries no stamp at all.

    Raises:
        ValueError: ``status`` is ``running`` and ``finished_at`` is given,
            because the check constraint would reject the row and the flush
            would fail with a database error instead of the reason.
    """
    if status == "running":
        if finished_at is not None:
            raise ValueError("a running ingestion run has no finished_at stamp")
    elif finished_at is None:
        finished_at = started_at

    row = IngestionRun(
        source=source,
        season_code=season_code,
        loader_version=loader_version,
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        rows_read=rows_read,
        rows_upserted=rows_upserted,
        rows_removed=rows_removed,
        rows_withdrawn=rows_withdrawn,
        rows_rejected=rows_rejected,
        content_sha256=content_sha256,
        error=error,
    )
    session.add(row)
    session.flush()
    return row


def create_rejections(
    session: Session,
    run: IngestionRun,
    *,
    count: int,
    reason: str = "unparsable score",
    raw: dict[str, object] | None = None,
) -> list[IngestionRejection]:
    """Insert ``count`` rejected rows for ``run`` and return them in order.

    ``row_number`` runs from 1 to ``count``, so a test can assert on the order
    the detail endpoint embeds. ``raw`` is the stored JSON object of the refused
    row and defaults to one that names the row number, which makes every
    embedded object distinguishable; a caller that passes one gets it on every
    row.
    """
    rows = [
        IngestionRejection(
            run_id=run.id,
            row_number=row_number,
            reason=reason,
            raw=raw if raw is not None else {"line": row_number},
        )
        for row_number in range(1, count + 1)
    ]
    session.add_all(rows)
    session.flush()
    return rows
