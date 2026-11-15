"""Season endpoints."""

from fastapi import APIRouter, HTTPException
from lmi_core.db.models import Season as SeasonModel
from sqlalchemy import select

from lmi_api.db import SessionDep
from lmi_api.schemas.seasons import Season

router = APIRouter(prefix="/seasons", tags=["seasons"])


def season_not_found(code: str) -> HTTPException:
    """Return the 404 error for a season ``code`` that is not stored.

    It returns the error instead of raising it so the caller raises it on the
    line that detected the miss, where the condition is visible. The message
    quotes the code the client sent, which is the client's own input.
    """
    return HTTPException(status_code=404, detail=f"Season '{code}' not found")


@router.get("")
def list_seasons(session: SessionDep) -> list[Season]:
    """Return every stored season, most recent first.

    Ordering by ``start_year`` descending puts the current season first, which
    is the one a dashboard opens on.

    The route path is empty on purpose: the router prefix is the whole path, so
    ``GET /v1/seasons`` is served directly instead of redirecting from
    ``/v1/seasons/``. The return annotation is the response model and the
    endpoint has no error path, so neither ``response_model`` nor ``responses``
    is passed.
    """
    rows = session.scalars(select(SeasonModel).order_by(SeasonModel.start_year.desc()))
    return [Season.model_validate(row) for row in rows]
