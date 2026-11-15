"""Ingestion run payloads: what a loader run recorded and what it rejected.

A run is the record of one execution of one source loader over one season. The
collection returns :class:`IngestionRunOut` rows; the single-run request returns
:class:`IngestionRunDetail`, which adds the source rows the loader refused.

Two nulls carry meaning and neither means "zero". ``finished_at`` is null
exactly while ``status`` is ``running``, so a client tells a run still in flight
from a finished one without reading the status first. ``content_sha256`` and
``error`` are null when the loader recorded none: a run over a source that
publishes no checksum has no hash, and a run that did not fail has no message.
"""

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel

from lmi_api.schemas.common import Page

IngestionSource = Literal["football_data", "openfootball"]
"""The two data sources a loader reads.

The database stores the same two spellings in a text column guarded by a check
constraint, and the literal repeats them so a generated client models the source
as an enumeration instead of a free string.
"""

IngestionRunStatus = Literal["running", "succeeded", "failed"]
"""The three states a recorded run can be in.

``running`` means the loader has not finished, which is why such a run carries
no ``finished_at``. ``succeeded`` and ``failed`` are terminal and carry one, and
a check constraint keeps the timestamp and the status in agreement in both
directions.
"""


class IngestionRunOut(BaseModel):
    """One recorded loader run.

    ``season_code`` is the season code the loader was asked to ingest, not a
    season identifier: the column is plain text with no foreign key, so a run of
    a season that is no longer stored stays readable.

    Every counter is the number of rows the loader read, upserted, removed,
    withdrew and rejected in that run, and none of them is null: a zero means
    the loader counted none, which is not the same as a count it did not record.
    """

    id: uuid.UUID
    source: IngestionSource
    season_code: str
    loader_version: str
    started_at: datetime.datetime
    finished_at: datetime.datetime | None
    status: IngestionRunStatus
    rows_read: int
    rows_upserted: int
    rows_removed: int
    rows_withdrawn: int
    rows_rejected: int
    content_sha256: str | None
    error: str | None


class IngestionRunPage(Page[IngestionRunOut]):
    """One page of recorded runs.

    The generic envelope is specialised here so the generated client and the
    OpenAPI document name a concrete schema, ``IngestionRunPage``, instead of an
    anonymous generic.
    """


class IngestionRejectionOut(BaseModel):
    """One source row a loader refused, with the reason it gave.

    ``raw`` is the stored JSON object of the refused row, so a client renders
    the values the loader saw without a second request. The column holds a JSON
    object, and the payload types it as one rather than as an arbitrary JSON
    value, because that is what every stored row is.
    """

    row_number: int
    reason: str
    raw: dict[str, Any]


class IngestionRunDetail(IngestionRunOut):
    """One recorded run with the rejections it kept.

    ``rejections_total`` is the number of rejected rows the run recorded, which
    is the count of every rejection row and not necessarily the size of
    ``rejections``: the embedded list is capped, so a client compares the two to
    tell a complete list from a truncated one.
    """

    rejections_total: int
    rejections: list[IngestionRejectionOut]
