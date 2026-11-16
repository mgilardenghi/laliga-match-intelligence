"""Prediction payloads: stored probabilities together with what they earned.

A stored prediction is immutable, so every value this module serves is either a
stored column or a value derived from the stored rows on read. The derived half
is ``evaluation`` and ``score``: both come from
:func:`lmi_core.live.scoring.evaluate`, the single place that decides whether a
prediction was on time and, when its match has a result, what its log loss,
Brier score, ranked probability score and hit are. No score table exists, so a
correction to that rule reaches every served row at once.

Two nulls carry meaning and neither means "zero". ``odds_source`` is null when
the match carried no eligible pre-closing price, which is the normal state of a
fixture that has not been played. ``score`` is null for every evaluation other
than ``scored``: a pending prediction, a superseded one, a late one and one
whose match was withdrawn were never scored, so there is no number to report and
a client must render "not scored" instead of a zero.

:class:`PredictionBrief` is the short form the team focus and the match preview
nest, so those resources describe a prediction with the same vocabulary the
collection uses rather than a second shape that could drift from it.
"""

import datetime
import uuid
from typing import Any, Literal

from lmi_core.results.schemas import ModelName
from pydantic import BaseModel, Field

from lmi_api.schemas.common import Page
from lmi_api.schemas.matches import MatchSummary

__all__ = [
    "Evaluation",
    "ModelName",
    "PredictionBrief",
    "PredictionDetail",
    "PredictionOut",
    "PredictionPage",
    "PredictionScore",
    "Probabilities",
]

Evaluation = Literal["pending", "scored", "superseded", "withdrawn", "late"]
"""What a stored prediction earned once its match reached a state.

``pending`` means the match has not been played and the row is the standing one
for its match and model, ``scored`` means the match has a result and the row was
on time, ``superseded`` means a later as-of date replaced the row, ``late``
means the row was stamped at or after Madrid midnight of the match day, and
``withdrawn`` means the source no longer lists the fixture. The five spellings
are the API spelling of ``lmi_core.live.schemas.PredictionOutcome``, which
decides them.
"""


class Probabilities(BaseModel):
    """The probability triple of one stored prediction.

    The three values are the stored columns, in the order the models emit them,
    and they sum to one within the tolerance the database check enforces. They
    are not renormalised on read: a served probability is exactly the number the
    worker stored.
    """

    home: float
    draw: float
    away: float


class PredictionScore(BaseModel):
    """The four scores of one on-time prediction of a played match.

    ``log_loss``, ``brier`` and ``rps`` are the M3 metric functions on this one
    row, and ``correct`` says whether the most likely outcome is the one that
    happened. The values are null-free because the object exists only for a
    scored row: an absent score is a null field on the parent, never an object
    with null numbers.
    """

    log_loss: float
    brier: float
    rps: float
    correct: bool


class PredictionOut(BaseModel):
    """One stored prediction of one match and model.

    ``match`` is the fixture the prediction is for, nested rather than
    referenced, because a client renders the club names and the kickoff date
    next to the probabilities and the endpoint reads them from the same request.
    ``as_of_date`` is the Madrid date the prediction was produced for and
    ``created_at`` the instant the database stored it: the two differ, and the
    evaluation depends on the second.

    ``odds_source`` names the bookmaker the forecast priced the match with, and
    is null when the match carried no eligible price. ``score`` is filled only
    for a ``scored`` row, so a client reads ``evaluation`` first and treats
    ``score`` as its consequence.
    """

    id: int
    match: MatchSummary
    model: ModelName
    model_version: str
    as_of_date: datetime.date
    created_at: datetime.datetime
    probabilities: Probabilities
    odds_source: str | None
    evaluation: Evaluation
    score: PredictionScore | None


class PredictionPage(Page[PredictionOut]):
    """One page of stored predictions.

    The generic envelope is specialised here so the generated client and the
    OpenAPI document name a concrete schema, ``PredictionPage``, instead of an
    anonymous generic.
    """


class PredictionDetail(PredictionOut):
    """One stored prediction with the run that wrote it and its configuration.

    ``run_id`` is the prediction run the row belongs to, and ``model_config`` is
    the configuration the model recorded at write time, so a reader can tell
    which settings produced the probabilities without a second request. The
    attribute is named ``config`` because pydantic reserves ``model_config`` for
    the configuration of the model class itself; the payload key is the
    documented ``model_config``, which the field carries as its alias.
    """

    run_id: uuid.UUID
    config: dict[str, Any] = Field(alias="model_config")


class PredictionBrief(BaseModel):
    """One prediction as the team focus and the match preview nest it.

    The shape is the part of :class:`PredictionOut` those two resources need:
    the identity a client follows to the detail endpoint, the model that
    produced the forecast, the moment and the as-of date, the probabilities and
    the evaluation. The match is deliberately absent, because both resources
    nest the brief inside the fixture it belongs to.
    """

    prediction_id: int
    model: ModelName
    model_version: str
    created_at: datetime.datetime
    as_of_date: datetime.date
    probabilities: Probabilities
    evaluation: Evaluation
