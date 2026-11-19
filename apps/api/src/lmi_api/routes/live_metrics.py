"""Live metric endpoint: the in-play scores of one season's predictions.

The endpoint is a read over the stored predictions and the stored results. It
introduces no rule of its own: the outcome of every stored row and every score
of the report come from :mod:`lmi_core.live.scoring`, and the market price comes
from the played-match loader with its margin removal, so the served numbers
are the ones the worker prints and cannot disagree with them.

There is one route and it is served at ``/live`` on the ``/model-metrics``
prefix, next to the stored backtest report. The path is fixed, so the two
resources share a prefix without sharing a route.
"""

from fastapi import APIRouter
from lmi_core.clock import madrid_today
from lmi_core.db.models import Match as MatchModel
from lmi_core.live.scoring import evaluate, live_metrics
from lmi_core.modeling.data import load_match_data
from sqlalchemy import select

from lmi_api import views
from lmi_api.db import SessionDep
from lmi_api.errors import PROBLEM_RESPONSES
from lmi_api.schemas.live_metrics import LiveMetricsResponse

router = APIRouter(prefix="/model-metrics", tags=["model-metrics"])


@router.get("/live", responses=PROBLEM_RESPONSES)
def get_live_model_metrics(session: SessionDep, season: str | None = None) -> LiveMetricsResponse:
    """Return the live metrics of one season, derived from the stored rows.

    Every number comes from :func:`lmi_core.live.scoring.live_metrics`, which is
    the same function the worker's ``predict --report`` prints through. This
    module only selects the rows it is given, so the endpoint and the worker
    cannot disagree about an outcome, a score or a count.

    ``season`` selects the matches whose predictions are scored. Without it the
    season that starts latest among the seasons with a match is served, exactly
    as the standings endpoint selects it, so both resources open on the season
    ingestion has loaded. The season bounds the match identifiers read, not the
    predictions table: a request for one season never scores another season's
    rows.

    ``market_reference`` is recomputed from the pre-closing 1x2 odds of the
    played matches, through the loader's bookmaker priority and margin removal. The
    prices are pre-kickoff information, but they were not stored as a live row,
    which is why the reference always reports ``stored_before_kickoff`` false
    and is a benchmark rather than a stored prediction.

    The report's ``evaluated_as_of`` is the Madrid date of the request, read
    from :func:`lmi_core.clock.madrid_today`, which is the same source the
    worker's publisher uses, so the two reports name the same evaluation day.

    ``season`` is a free string and rejects no value before the lookup, so the
    only error this route raises is the 404 of an unknown code; the shared
    problem responses still declare the 422 a query validation could produce.

    Raises:
        HTTPException: 404 when ``season`` is given and no season has that code,
            or when no season has a match while ``season`` is absent. The detail
            is a fixed sentence and never repeats the client value.
    """
    season_id, _code = views.resolve_season(session, code=season)
    season_match_ids = session.scalars(
        select(MatchModel.id).where(MatchModel.season_id == season_id)
    ).all()
    stored = views.load_stored_predictions(session, match_ids=season_match_ids)
    evaluated = evaluate([entry.row for entry in stored])
    data = load_match_data(session)
    odds_by_match = {
        int(match_id): odds for match_id, odds in zip(data.match_id, data.odds, strict=True)
    }
    return live_metrics(evaluated, odds_by_match, today=madrid_today())
