"""Pydantic DTOs for the live-prediction contract.

The same shapes are shared by three callers: the worker's ``predict --report``
output, the future ``GET /v1/predictions`` endpoint and the live metrics
endpoint. The field names and the optional fields therefore change only
together.

A stored prediction is immutable, so its identity is the natural key
``(match_id, model, as_of_date)`` plus the ``created_at`` the database stamps.
:class:`PredictionRow` flattens that identity with the match it belongs to,
because every consumer needs both halves and a second nested DTO would only add
a wrapper.
"""

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

PredictionOutcome = Literal["scored", "pending", "superseded", "late", "withdrawn"]
"""What happened to a stored prediction once its match reached a final state."""


class PredictionRow(BaseModel):
    """One stored prediction joined with the match it belongs to.

    The identity fields are the immutable ones the database stores; the match
    columns let a reader decide the outcome without a second query. ``result``
    is ``None`` for a match that has not been played, so a pending prediction
    cannot accidentally be read as scored.
    """

    model_config = ConfigDict(frozen=True)

    match_id: int = Field(description="The match the prediction is for.")
    model: str = Field(description="The registered model name.")
    model_version: str = Field(description="The model version recorded at write time.")
    as_of_date: date = Field(description="The Madrid date the prediction was produced for.")
    created_at: datetime = Field(description="The instant the database stored the row, aware.")
    p_home: float = Field(description="Probability of a home win.")
    p_draw: float = Field(description="Probability of a draw.")
    p_away: float = Field(description="Probability of an away win.")
    kickoff_date: date = Field(description="The Madrid calendar date of the match.")
    match_status: Literal["scheduled", "played", "withdrawn"] = Field(
        description="The stored status of the match."
    )
    result: Literal["H", "D", "A"] | None = Field(
        description="The stored result, null unless the match is played."
    )


class EvaluatedPrediction(BaseModel):
    """One stored prediction with the outcome it earned.

    The scores are populated together only when the prediction was on time and
    the match has a result: an outcome of ``scored`` means every score field is
    filled, and every other outcome means all of them are null. The DTO is flat
    on purpose, so a report table can be built without walking a nested model.
    """

    model_config = ConfigDict(frozen=True)

    match_id: int = Field(description="The match the prediction is for.")
    model: str = Field(description="The registered model name.")
    model_version: str = Field(description="The model version recorded at write time.")
    as_of_date: date = Field(description="The Madrid date the prediction was produced for.")
    created_at: datetime = Field(description="The instant the database stored the row, aware.")
    p_home: float = Field(description="Probability of a home win.")
    p_draw: float = Field(description="Probability of a draw.")
    p_away: float = Field(description="Probability of an away win.")
    kickoff_date: date = Field(description="The Madrid calendar date of the match.")
    match_status: Literal["scheduled", "played", "withdrawn"] = Field(
        description="The stored status of the match."
    )
    result: Literal["H", "D", "A"] | None = Field(
        description="The stored result, null unless the match is played."
    )
    outcome: PredictionOutcome = Field(description="What happened to the prediction.")
    log_loss: float | None = Field(description="Log loss, null unless the outcome is scored.")
    brier: float | None = Field(description="Brier score, null unless the outcome is scored.")
    rps: float | None = Field(description="Ranked probability score, null unless scored.")
    correct: bool | None = Field(
        description="Whether the highest-probability outcome won, null unless scored."
    )

    @model_validator(mode="after")
    def _scores_belong_to_scored_rows(self) -> Self:
        """Reject a row whose outcome and scores contradict each other."""
        scores = (self.log_loss, self.brier, self.rps, self.correct)
        if self.outcome == "scored":
            if any(value is None for value in scores):
                raise ValueError("a scored prediction must carry every score")
        elif any(value is not None for value in scores):
            raise ValueError(f"a {self.outcome} prediction must not carry a score")
        return self


class MetricScores(BaseModel):
    """The aggregate scores of one model on one match set.

    The scores are computed over the same matched rows, so ``n_matches`` is the
    single count a reader needs to judge how much the numbers mean.
    """

    model_config = ConfigDict(frozen=True)

    n_matches: int = Field(ge=0, description="The number of scored matches.")
    log_loss: float = Field(ge=0, description="Mean log loss over the scored matches.")
    brier: float = Field(ge=0, description="Mean Brier score over the scored matches.")
    rps: float = Field(ge=0, description="Mean ranked probability score.")
    accuracy: float = Field(ge=0, le=1, description="Share of correct top outcomes.")


class ModelMetrics(BaseModel):
    """The live metrics of one model and one version.

    The version is part of the identity because two rows of a model with
    different versions are not comparable and must not be pooled.
    """

    model_config = ConfigDict(frozen=True)

    model: str = Field(description="The registered model name.")
    model_version: str = Field(description="The model version the rows were produced by.")
    scores: MetricScores = Field(description="The aggregate scores of this model.")


class CommonSubset(BaseModel):
    """The metrics of every model on the matches they all predicted.

    Only this subset compares models fairly: the ``models`` list of the
    response is computed on each model's own scored rows, which may differ.
    """

    model_config = ConfigDict(frozen=True)

    match_count: int = Field(ge=0, description="The size of the shared match set.")
    models: tuple[ModelMetrics, ...] = Field(
        description="One entry per model, in the requested model order."
    )


class MarketReference(BaseModel):
    """The market as a reference, scored on the same stored-prediction rows.

    The odds are pre-kickoff information, so the market price is a legitimate
    comparison. It was not stored live, though: the reference is recomputed from
    the odds of a played match, which means it is a benchmark, not a prediction
    the worker stored before kickoff. ``stored_before_kickoff`` is therefore
    always ``False`` and never claims otherwise.
    """

    model_config = ConfigDict(frozen=True)

    model: str = Field(description="The reference model name; always ``market``.")
    model_version: str = Field(description="The market model version.")
    scores: MetricScores = Field(description="The market's aggregate scores.")
    stored_before_kickoff: bool = Field(
        description="Always false: the reference is recomputed, not a stored live row."
    )
    match_count: int = Field(ge=0, description="The number of matches the reference covers.")
    matches_without_odds: int = Field(
        ge=0, description="The matches the reference had to skip for missing odds."
    )


class PredictionCounts(BaseModel):
    """The per-outcome counts of the evaluated prediction rows.

    The counts explain which subset the metrics cover: only ``scored`` rows have
    scores, and the remaining rows are the reason a model's coverage is below
    one.
    """

    model_config = ConfigDict(frozen=True)

    n_rows: int = Field(ge=0, description="Every evaluated prediction row.")
    n_scored: int = Field(ge=0, description="Rows with a result and an on-time prediction.")
    n_pending: int = Field(ge=0, description="Rows whose match has not been played.")
    n_superseded: int = Field(ge=0, description="Rows replaced by a later as-of date.")
    n_late: int = Field(ge=0, description="Rows stored at or after the match date.")
    n_withdrawn: int = Field(ge=0, description="Rows whose match was withdrawn.")


class LiveMetricsResponse(BaseModel):
    """The live metrics report: per-model scores, the common subset and counts.

    The response exists so the API and the worker report share one shape. The
    ``models`` list is per model and may cover different match sets, ``common``
    is the fair comparison and is null when no match is shared, and
    ``market_reference`` is null when no odds are available.
    """

    model_config = ConfigDict(frozen=True)

    evaluated_as_of: date = Field(description="The Madrid date the report was evaluated at.")
    models: tuple[ModelMetrics, ...] = Field(description="One entry per stored model.")
    common: CommonSubset | None = Field(
        description="The shared-match comparison, null when no match is shared."
    )
    market_reference: MarketReference | None = Field(
        description="The market benchmark, null when no odds are available."
    )
    counts: PredictionCounts = Field(description="The outcome counts of the evaluated rows.")
