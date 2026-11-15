"""Ingestion run endpoints: the recorded history of the data loader.

A run is written by a worker loader and only updated once, when the loader
records its finish, so this module reads and never writes. The collection
exposes the run history with the filters a dashboard asks for; the single-run
request adds the source rows the loader refused.

``season_code`` on a run is plain text with no foreign key: the table records
what a loader was asked to ingest, including a run whose season was never
stored. The list therefore probes the ``seasons`` table before it pages, so a
season filter that names no stored season is a 404 whatever the runs carry.
"""

import uuid
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query
from lmi_core.db.models import IngestionRejection as IngestionRejectionModel
from lmi_core.db.models import IngestionRun as IngestionRunModel
from lmi_core.db.models import Season as SeasonModel
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from lmi_api.db import SessionDep
from lmi_api.errors import PROBLEM_RESPONSES
from lmi_api.schemas.ingestion_runs import (
    IngestionRejectionOut,
    IngestionRunDetail,
    IngestionRunOut,
    IngestionRunPage,
    IngestionRunStatus,
    IngestionSource,
)

router = APIRouter(prefix="/ingestion-runs", tags=["ingestion-runs"])

REJECTION_PAGE_SIZE = 200
"""The number of rejections the detail endpoint embeds at most.

A single run can reject thousands of rows, and one response that carries all of
them grows without bound. The detail therefore embeds the first
``REJECTION_PAGE_SIZE`` rejections in ``row_number`` order and reports the full
count in ``rejections_total``, so a client tells a complete list from a capped
one without a second request per row.
"""


@router.get("", responses=PROBLEM_RESPONSES)
def list_ingestion_runs(
    session: SessionDep,
    source: IngestionSource | None = None,
    season: str | None = None,
    status: IngestionRunStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> IngestionRunPage:
    """Return one page of recorded runs, most recently started first.

    Every filter is optional and the ones given are combined with ``AND``: a
    data source, a season code and a run status. ``source`` and ``status`` are
    literals, so a value outside the two sources or the three statuses is
    rejected by the request validation and never reaches this function.

    ``total`` is the number of runs the filter matched in the whole table, not
    the size of ``items``, so a client renders a result count and asks for the
    next page without a second call. ``limit`` and ``offset`` are echoed from
    the request.

    The order is the start instant descending, with the identifier descending as
    a tie-break: two runs that started in the same instant then still have one
    order, so paging the same filter twice cannot move a row between pages.

    The season filter is probed against the ``seasons`` table before the page is
    read. The run table keeps the code as plain text, so a code no season has
    would otherwise be a valid filter value that merely matched no row, and the
    answer would depend on whether some run happens to carry that code.

    The route path is empty on purpose: the router prefix is the whole path, so
    ``GET /v1/ingestion-runs`` is served directly instead of redirecting from
    ``/v1/ingestion-runs/``. The return annotation is the response model, so no
    ``response_model`` is passed.

    The request issues three statements: the season probe when a season code is
    given, one count and one page. The page statement selects the whole run row
    and the payload is built from it field by field, because the models declare
    no relationship the code could lazy-load per row.

    Raises:
        HTTPException: 404 when ``season`` names no stored season. The detail is
            a fixed sentence and never repeats the value the client sent.
    """
    if season is not None:
        _raise_if_season_is_unknown(session, season)

    conditions: list[ColumnElement[bool]] = []
    if source is not None:
        conditions.append(IngestionRunModel.source == source)
    if season is not None:
        conditions.append(IngestionRunModel.season_code == season)
    if status is not None:
        conditions.append(IngestionRunModel.status == status)

    count_statement = select(func.count()).select_from(IngestionRunModel).where(*conditions)
    page_statement = (
        select(IngestionRunModel)
        .where(*conditions)
        .order_by(
            IngestionRunModel.started_at.desc(),
            IngestionRunModel.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    total = session.scalar(count_statement)
    rows = session.scalars(page_statement).all()
    return IngestionRunPage(
        items=[_run_out(row) for row in rows],
        total=total if total is not None else 0,
        limit=limit,
        offset=offset,
    )


@router.get("/{run_id}", responses=PROBLEM_RESPONSES)
def get_ingestion_run(run_id: uuid.UUID, session: SessionDep) -> IngestionRunDetail:
    """Return one recorded run with the source rows it rejected.

    The rejections are the first ``REJECTION_PAGE_SIZE`` of the run in
    ``row_number`` order, with the identifier as the tie-break, and
    ``rejections_total`` is the count of every rejection of the run. A client
    therefore knows whether the embedded list is complete, and no rejection
    costs a second statement: the count and the page are one statement each,
    whatever the size of the run.

    The return annotation is the response model, so no ``response_model`` is
    passed. The payload of the run itself is the one the collection returns,
    built by the same function, and the rejections are appended to it.

    Raises:
        HTTPException: 404 when no stored run has ``run_id``. The detail is a
            fixed sentence and never repeats the identifier.
    """
    run = session.scalar(select(IngestionRunModel).where(IngestionRunModel.id == run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Ingestion run not found")

    count_statement = (
        select(func.count())
        .select_from(IngestionRejectionModel)
        .where(IngestionRejectionModel.run_id == run_id)
    )
    rejection_statement = (
        select(IngestionRejectionModel)
        .where(IngestionRejectionModel.run_id == run_id)
        .order_by(
            IngestionRejectionModel.row_number.asc(),
            IngestionRejectionModel.id.asc(),
        )
        .limit(REJECTION_PAGE_SIZE)
    )
    rejections_total = session.scalar(count_statement)
    rejections = session.scalars(rejection_statement).all()
    return IngestionRunDetail(
        **_run_out(run).model_dump(),
        rejections_total=rejections_total if rejections_total is not None else 0,
        rejections=[
            IngestionRejectionOut(
                row_number=rejection.row_number,
                reason=rejection.reason,
                raw=rejection.raw,
            )
            for rejection in rejections
        ],
    )


def _raise_if_season_is_unknown(session: Session, code: str) -> None:
    """Raise the 404 of a season filter that names no stored season.

    It runs before the page statement, because the count alone cannot tell an
    unknown code from a known one that matches no run. The detail is a fixed
    sentence: the value the client sent is not repeated, unlike the season
    endpoints, which quote their own path parameter because that is the resource
    being addressed.

    Raises:
        HTTPException: no season has ``code``.
    """
    stored_code = session.scalar(select(SeasonModel.code).where(SeasonModel.code == code))
    if stored_code is None:
        raise HTTPException(status_code=404, detail="Season not found")


def _run_out(run: IngestionRunModel) -> IngestionRunOut:
    """Return the payload of one stored run row.

    The row is built from the columns of the row the statement selected, never
    from a relationship: the models declare none, and reading an attribute the
    statement did not load would issue one query per row.

    ``source`` and ``status`` are narrowed with a cast because the ORM types
    them as plain text: a check constraint restricts them to the values of their
    literal, so the cast states a rule the database already enforces.
    """
    return IngestionRunOut(
        id=run.id,
        source=cast(IngestionSource, run.source),
        season_code=run.season_code,
        loader_version=run.loader_version,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=cast(IngestionRunStatus, run.status),
        rows_read=run.rows_read,
        rows_upserted=run.rows_upserted,
        rows_removed=run.rows_removed,
        rows_withdrawn=run.rows_withdrawn,
        rows_rejected=run.rows_rejected,
        content_sha256=run.content_sha256,
        error=run.error,
    )
