"""Prediction endpoints: the stored live predictions of the league.

The endpoints are read-only, because a stored prediction is immutable: the
worker writes it before kickoff and nothing rewrites it. What the API adds is
the evaluation: whether a stored row was on time, and, when its match has a
result, its four scores. Both are derived on read by
:func:`lmi_core.live.scoring.evaluate`, so this module holds no scoring rule of
its own and the served numbers agree with the live metrics endpoint by
construction.

The collection pages and filters the ledger; the single prediction returns one
row with the run and the model configuration that produced it.
"""

import datetime
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from lmi_core.db.models import Match as MatchModel
from lmi_core.db.models import Prediction as PredictionModel
from lmi_core.db.models import Team as TeamModel
from lmi_core.live.schemas import EvaluatedPrediction
from lmi_core.live.scoring import evaluate
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from lmi_api import views
from lmi_api.db import SessionDep
from lmi_api.errors import PROBLEM_RESPONSES
from lmi_api.schemas.matches import MatchStatus, MatchSummary
from lmi_api.schemas.predictions import (
    Evaluation,
    ModelName,
    PredictionDetail,
    PredictionOut,
    PredictionPage,
    PredictionScore,
    Probabilities,
)

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("", responses=PROBLEM_RESPONSES)
def list_predictions(
    session: SessionDep,
    season: str | None = None,
    match_id: int | None = None,
    team_id: int | None = None,
    model: ModelName | None = None,
    match_status: MatchStatus | None = None,
    evaluation: Evaluation | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    latest_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PredictionPage:
    """Return one page of stored predictions with the outcome each one earned.

    Every filter is optional and the ones given are combined with ``AND``. A
    season code selects that season; without one the season that starts latest
    among the seasons with a match is served, exactly as the standings endpoint
    selects it. ``match_id`` restricts the page to one fixture and ``team_id``
    to the fixtures a team played home or away, ``model`` to one registered
    model name, ``match_status`` to the current status of the match, and the two
    dates to an inclusive range of ``kickoff_date``, so a range covers whole
    days.

    The two derived filters are ordered, and the order is part of the contract.
    ``latest_only`` projects the rows the *other* filters selected onto one
    standing row per ``(match, model)``, and ``evaluation`` then restricts that
    projection. So ``evaluation=superseded`` with ``latest_only=true`` is
    normally empty, because a superseded row never stands for its group, while
    ``latest_only=false`` is the whole ledger the other filters selected and
    returns every superseded row. The default answers the question a dashboard
    asks, one forecast per match and model; ``latest_only=false`` answers what
    the worker stored.

    ``total`` is the number of rows the filters matched before the page was
    sliced, not the size of ``items``, so a client renders a result count and
    asks for the next page without a second call. The order is the kickoff date
    ascending, then the match identifier, then the model in the registry order
    and then the as-of date descending: a match with two models lists them in
    report order, and the ledger of one model lists its newest row first.

    The request issues a fixed number of statements whatever the size of the
    page: the season resolution, at most one existence probe per filter value
    given, one statement for the match identifiers of the season, one for the
    stored predictions and one for the match summaries of the page. The
    summaries cover the rows on the page only, so the response does not read the
    fixture of every match the filters matched.

    The route path is empty on purpose: the router prefix is the whole path, so
    ``GET /v1/predictions`` is served directly instead of redirecting from
    ``/v1/predictions/``. The return annotation is the response model, so no
    ``response_model`` is passed.

    Raises:
        HTTPException: 404 when ``season`` names no stored season, or when
            ``match_id`` or ``team_id`` names no stored row. The detail is a
            fixed sentence in every case and never repeats the value the client
            sent.
        RequestValidationError: 422 when ``date_from`` is after ``date_to``,
            rendered by the installed handler as a problem details body that
            names the offending parameter and not the dates.
    """
    if date_from is not None and date_to is not None and date_from > date_to:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("query", "date_to"),
                    "msg": "date_from must not be after date_to",
                }
            ]
        )

    season_id, _season_code = views.resolve_season(session, code=season)
    _raise_if_filter_value_is_unknown(session, match_id=match_id, team_id=team_id)

    match_statement = select(MatchModel.id).where(MatchModel.season_id == season_id)
    if match_id is not None:
        match_statement = match_statement.where(MatchModel.id == match_id)
    if team_id is not None:
        match_statement = match_statement.where(
            or_(MatchModel.home_team_id == team_id, MatchModel.away_team_id == team_id)
        )
    season_match_ids = list(session.scalars(match_statement))

    stored = views.load_stored_predictions(
        session,
        match_ids=season_match_ids,
        model=model,
        match_status=match_status,
        date_from=date_from,
        date_to=date_to,
    )
    stored_by_identity = {entry.identity: entry for entry in stored}
    served = evaluate([entry.row for entry in stored])
    if latest_only:
        served = views.standing_predictions(served)
    if evaluation is not None:
        served = [row for row in served if row.outcome == evaluation]

    ordered = sorted(served, key=_list_order)
    page = ordered[offset : offset + limit]
    matches = views.load_match_summaries(session, [row.match_id for row in page])
    return PredictionPage(
        items=[
            _prediction_out(
                row,
                stored_by_identity[(row.match_id, row.model, row.as_of_date)],
                matches[row.match_id],
            )
            for row in page
        ],
        total=len(ordered),
        limit=limit,
        offset=offset,
    )


@router.get("/{prediction_id}", responses=PROBLEM_RESPONSES)
def get_prediction(prediction_id: int, session: SessionDep) -> PredictionDetail:
    """Return one stored prediction with the run that wrote it.

    The stored row is read by identifier, and then every stored prediction of
    the same match is read and evaluated, so the served ``evaluation`` and
    ``score`` are the group-correct ones: a row that a later as-of date replaced
    is ``superseded`` even when its match already has a result, which is exactly
    what the collection reports for it. Scoring the single row on its own would
    call it ``scored`` and disagree with the ledger the collection serves.

    The detail adds the two columns the collection drops: ``run_id``, the
    prediction run of the row, and ``model_config``, the configuration the model
    recorded at write time. The payload is built through ``model_validate``
    because the configuration field carries the documented ``model_config`` as
    its alias: pydantic reserves that attribute name for the model class
    configuration.

    The return annotation is the response model, so no ``response_model`` is
    passed.

    Raises:
        HTTPException: 404 when no stored prediction has ``prediction_id``. The
            detail is a fixed sentence and never repeats the identifier.
    """
    stored_row = session.scalar(select(PredictionModel).where(PredictionModel.id == prediction_id))
    if stored_row is None:
        raise HTTPException(status_code=404, detail="Prediction not found")

    identity = (stored_row.match_id, stored_row.model, stored_row.as_of_date)
    group = views.load_stored_predictions(session, match_ids=[stored_row.match_id])
    stored = {entry.identity: entry for entry in group}[identity]
    # ``evaluate`` returns one row per input row, so this lookup cannot miss.
    served = {_identity(row): row for row in evaluate([entry.row for entry in group])}[identity]
    matches = views.load_match_summaries(session, [stored_row.match_id])
    payload = _prediction_out(served, stored, matches[stored_row.match_id]).model_dump()
    return PredictionDetail.model_validate(
        {**payload, "run_id": stored.run_id, "model_config": stored.model_config}
    )


def _raise_if_filter_value_is_unknown(
    session: Session, *, match_id: int | None, team_id: int | None
) -> None:
    """Raise the 404 of a filter value that names no stored row.

    The probes run before the rows are read, because the page can be empty for
    two different reasons: an unknown value, and a known one that matches no
    prediction. Only the probes tell them apart, and a client that sent a value
    naming nothing needs the 404 rather than an empty page. The detail is a
    fixed sentence: the value the client sent is not repeated, unlike the match
    and team endpoints, which quote their own path parameter because that is the
    resource being addressed.

    Raises:
        HTTPException: ``match_id`` is set and no match has that identifier, or
            ``team_id`` is set and no team has that identifier.
    """
    if match_id is not None:
        stored_match = session.scalar(select(MatchModel.id).where(MatchModel.id == match_id))
        if stored_match is None:
            raise HTTPException(status_code=404, detail="Match not found")
    if team_id is not None:
        stored_team = session.scalar(select(TeamModel.id).where(TeamModel.id == team_id))
        if stored_team is None:
            raise HTTPException(status_code=404, detail="Team not found")


def _identity(row: EvaluatedPrediction) -> tuple[int, str, datetime.date]:
    """Return the natural key ``(match_id, model, as_of_date)`` of an evaluated row."""
    return (row.match_id, row.model, row.as_of_date)


def _list_order(row: EvaluatedPrediction) -> tuple[datetime.date, int, int, int]:
    """Return the page order of one row.

    The kickoff date and the match identifier sort ascending, the model in
    registry order, and the as-of date descending: the date is negated through
    its ordinal because it is the only key that reverses.
    """
    return (
        row.kickoff_date,
        row.match_id,
        views.model_position(row.model),
        -row.as_of_date.toordinal(),
    )


def _prediction_out(
    row: EvaluatedPrediction, stored: views.StoredPrediction, match: MatchSummary
) -> PredictionOut:
    """Return the payload of one evaluated row and its stored identity.

    ``evaluation`` is the core outcome and ``score`` is filled only for a
    ``scored`` row, which the core DTO already guarantees to carry all four
    scores, so the casts below narrow what :func:`evaluate` decided.
    """
    return PredictionOut(
        id=stored.id,
        match=match,
        model=cast(ModelName, row.model),
        model_version=row.model_version,
        as_of_date=row.as_of_date,
        created_at=row.created_at,
        probabilities=Probabilities(home=row.p_home, draw=row.p_draw, away=row.p_away),
        odds_source=stored.odds_source,
        evaluation=row.outcome,
        score=_score(row),
    )


def _score(row: EvaluatedPrediction) -> PredictionScore | None:
    """Return the four scores of a scored row, and ``None`` for every other outcome."""
    if row.outcome != "scored":
        return None
    return PredictionScore(
        log_loss=cast(float, row.log_loss),
        brier=cast(float, row.brier),
        rps=cast(float, row.rps),
        correct=cast(bool, row.correct),
    )
