"""Model metric endpoints: the stored quality metrics of a backtest run."""

import uuid

from fastapi import APIRouter, HTTPException
from lmi_core.results.store import load_report

from lmi_api.db import SessionDep
from lmi_api.errors import PROBLEM_RESPONSES
from lmi_api.schemas.model_metrics import ModelMetricsResponse

router = APIRouter(prefix="/model-metrics", tags=["model-metrics"])


@router.get("", responses=PROBLEM_RESPONSES)
def get_model_metrics(session: SessionDep, run_id: uuid.UUID | None = None) -> ModelMetricsResponse:
    """Return the stored report of one backtest run, or of the latest one.

    The resource is the whole result of a walk-forward backtest: the run header,
    the models it scored, their metrics and their calibration bins. ``run_id``
    selects a stored run; without it the latest succeeded run is served, which
    is the one a dashboard opens on. The report is returned as the store orders
    it, so the API adds no second definition of the contract order.

    Only a succeeded run is served. :func:`~lmi_core.results.store.load_report`
    returns ``None`` both when no succeeded run is stored and when ``run_id``
    names a run that is missing, still ``running`` or ``failed``, so every miss
    is the same 404 and the response never discloses which ids are in flight.

    The route path is empty on purpose: the router prefix is the whole path, so
    ``GET /v1/model-metrics`` is served directly instead of redirecting from
    ``/v1/model-metrics/``. The return annotation is the response model, so no
    ``response_model`` is passed. A malformed ``run_id`` is rejected by the
    query validation and rendered by the installed handler, so only the 404 is
    raised here.

    Raises:
        HTTPException: 404 when no succeeded run matches the request.
    """
    report = load_report(session, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="no backtest run")
    return report
