"""Derive the live score of every stored prediction and aggregate the metrics.

No score is stored. :func:`evaluate` turns the stored prediction rows into the
outcome each one earned, and :func:`live_metrics` aggregates the scored rows
into the report the API serves. Both functions are pure: they read no clock, no
database and no configuration, so the same rows always produce the same report.

The outcome of a prediction depends on a comparison between two instants: when
the database stored the row, and the Madrid midnight that opened the match day.
A row stamped before that boundary could not know the match, so it is valid; a
row stamped at or after it is ``late`` and never scored. The comparison uses the
match's **current** ``kickoff_date``, so a match moved earlier than the creation
instant makes the row invalid even though it was stored in good faith.

The scores themselves are the M3 metric functions on a one-row array, so the
log-loss clip is :data:`lmi_core.evaluation.metrics.EPS` and never a second
constant that could drift away from it.
"""

import datetime
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from lmi_core.clock import kickoff_boundary
from lmi_core.evaluation.metrics import accuracy, brier, log_loss, rps
from lmi_core.live.schemas import (
    CommonSubset,
    EvaluatedPrediction,
    LiveMetricsResponse,
    MarketReference,
    MetricScores,
    ModelMetrics,
    PredictionCounts,
    PredictionOutcome,
    PredictionRow,
)
from lmi_core.modeling.market import remove_margin
from lmi_core.modeling.registry import MODEL_NAMES, make_model
from lmi_core.modeling.types import OUTCOMES

_RESULT_INDEX: Final[Mapping[str, int]] = {name: index for index, name in enumerate(OUTCOMES)}
"""The outcome index each result letter maps to, in ``H``, ``D``, ``A`` order."""

_MODEL_ORDER: Final[Mapping[str, int]] = {name: index for index, name in enumerate(MODEL_NAMES)}
"""Report position of each model name; unknown names sort last."""

_COLUMNS: Final = len(OUTCOMES)
"""How many outcomes one probability row carries."""

_LATE: Final[PredictionOutcome] = "late"
_SUPERSEDED: Final[PredictionOutcome] = "superseded"
_SCORED: Final[PredictionOutcome] = "scored"
_PENDING: Final[PredictionOutcome] = "pending"
_WITHDRAWN: Final[PredictionOutcome] = "withdrawn"


@dataclass(frozen=True)
class _Scores:
    """The four scores of one on-time prediction, before they become a DTO."""

    log_loss: float
    brier: float
    rps: float
    correct: bool


def evaluate(rows: Sequence[PredictionRow]) -> list[EvaluatedPrediction]:
    """Return every stored prediction with the outcome it earned.

    The rows are grouped by ``(match_id, model)`` and judged per group, because
    the outcome is a property of one match and one model: only the latest
    on-time row of a group can still be scored or pending, and every earlier one
    was replaced by it.

    The rules, in order:

    * a ``withdrawn`` match gives every one of its rows ``withdrawn`` and no
      score;
    * a row created at or after :func:`lmi_core.clock.kickoff_boundary` of the
      match's current date is ``late`` and carries no score;
    * otherwise the latest valid row by ``(as_of_date, created_at)`` is
      ``scored`` when the match is ``played`` and ``pending`` when it is
      ``scheduled``, and every other valid row is ``superseded``.

    The result is sorted by ``(match_id, model, as_of_date, created_at)``, so a
    caller gets the same list for the same input.

    Args:
        rows: the stored predictions joined with their match.

    Returns:
        One :class:`~lmi_core.live.schemas.EvaluatedPrediction` per input row.

    Raises:
        ValueError: a row whose match is ``played`` has no result. The loader
            already refuses that input; dropping the row silently here would
            hide a broken read.
    """
    groups: dict[tuple[int, str], list[PredictionRow]] = defaultdict(list)
    for row in rows:
        if row.match_status == "played" and row.result is None:
            raise ValueError(
                f"played match {row.match_id} has no result; its prediction cannot be evaluated"
            )
        groups[(row.match_id, row.model)].append(row)

    evaluated: list[EvaluatedPrediction] = []
    for group in groups.values():
        evaluated.extend(_evaluate_group(group))
    evaluated.sort(key=lambda row: (row.match_id, row.model, row.as_of_date, row.created_at))
    return evaluated


def live_metrics(
    evaluated: Sequence[EvaluatedPrediction],
    odds_by_match: Mapping[int, NDArray[np.float64]],
    *,
    today: datetime.date,
) -> LiveMetricsResponse:
    """Aggregate the evaluated predictions into the live metrics report.

    ``today`` is the Madrid date the report describes and only fills
    ``evaluated_as_of``; it decides no outcome, so the same rows produce the same
    scores on any date.

    ``models`` aggregates the ``scored`` rows per ``(model, model_version)``,
    each on its own match set. ``common`` re-scores every model name on the
    matches all of them predicted, which is the only fair comparison, and is
    ``None`` when no match is shared or nothing was scored. ``market_reference``
    recomputes the market from the stored decimal odds, through
    :func:`lmi_core.modeling.market.remove_margin`, on exactly the shared match
    set ``common`` reports rather than on a wider one: it was not stored before
    kickoff, so the two numbers are only comparable when they cover the same
    matches. ``counts`` describes every evaluated row.

    Args:
        evaluated: the rows returned by :func:`evaluate`.
        odds_by_match: decimal ``H/D/A`` odds per match id, normally the
            pre-closing prices the fixture carried. An absent entry and an
            all-NaN row both mean the match has no usable price.
        today: the Madrid date the report is evaluated at.

    Returns:
        The report, with ``models`` ordered by :data:`MODEL_NAMES` and then
        ``model_version``, unknown names last.

    Raises:
        ValueError: an odds entry does not have shape ``(3,)`` or holds a
            partial NaN row, which no caller can mean.
    """
    scored = [row for row in evaluated if row.outcome == _SCORED]

    by_model: dict[tuple[str, str], list[EvaluatedPrediction]] = defaultdict(list)
    for row in scored:
        by_model[(row.model, row.model_version)].append(row)

    shared = _shared_matches(by_model)
    models = tuple(
        ModelMetrics(model=model, model_version=version, scores=_metric_scores(rows))
        for (model, version), rows in sorted(by_model.items(), key=_model_key)
    )

    return LiveMetricsResponse(
        evaluated_as_of=today,
        models=models,
        common=_common(by_model, shared),
        market_reference=_market_reference(scored, odds_by_match, shared),
        counts=_counts(evaluated),
    )


def _evaluate_group(group: Sequence[PredictionRow]) -> list[EvaluatedPrediction]:
    """Return the evaluated rows of one ``(match_id, model)`` group."""
    if group[0].match_status == "withdrawn":
        return [_evaluated(row, _WITHDRAWN) for row in group]

    boundary = kickoff_boundary(group[0].kickoff_date)
    ordered = sorted(group, key=lambda row: (row.as_of_date, row.created_at), reverse=True)
    valid = [row for row in ordered if row.created_at < boundary]
    latest = valid[0] if valid else None
    current: PredictionOutcome = _SCORED if group[0].match_status == "played" else _PENDING

    evaluated: list[EvaluatedPrediction] = []
    for row in ordered:
        if row.created_at >= boundary:
            evaluated.append(_evaluated(row, _LATE))
        elif row is latest:
            evaluated.append(_evaluated(row, current))
        else:
            evaluated.append(_evaluated(row, _SUPERSEDED))
    return evaluated


def _evaluated(row: PredictionRow, outcome: PredictionOutcome) -> EvaluatedPrediction:
    """Build the DTO of one row, with the scores an outcome earns.

    Only ``scored`` carries scores; every other outcome leaves the four fields
    null, which the DTO validator enforces as well.
    """
    scores = _score_row(row) if outcome == _SCORED else None
    return EvaluatedPrediction(
        **row.model_dump(),
        outcome=outcome,
        log_loss=None if scores is None else scores.log_loss,
        brier=None if scores is None else scores.brier,
        rps=None if scores is None else scores.rps,
        correct=None if scores is None else scores.correct,
    )


def _score_row(row: PredictionRow) -> _Scores:
    """Return the four scores of one on-time prediction of a played match."""
    probabilities = np.array([[row.p_home, row.p_draw, row.p_away]], dtype=np.float64)
    results = np.array([_result_index(row.match_id, row.result)], dtype=np.int8)
    return _Scores(
        log_loss=log_loss(probabilities, results),
        brier=brier(probabilities, results),
        rps=rps(probabilities, results),
        correct=bool(accuracy(probabilities, results)),
    )


def _result_index(match_id: int, result: str | None) -> int:
    """Return the ``H``, ``D``, ``A`` index of a stored result.

    Raises:
        ValueError: the result is missing or is not one of the three outcomes.
    """
    if result is None:
        raise ValueError(f"match {match_id} has no result; it cannot be scored")
    index = _RESULT_INDEX.get(result)
    if index is None:
        raise ValueError(
            f"match {match_id} has result {result!r}; expected one of " + ", ".join(OUTCOMES)
        )
    return index


def _metric_scores(rows: Sequence[EvaluatedPrediction]) -> MetricScores:
    """Aggregate the scored rows into one :class:`MetricScores`."""
    probabilities = np.array(
        [[row.p_home, row.p_draw, row.p_away] for row in rows], dtype=np.float64
    )
    results = np.array([_result_index(row.match_id, row.result) for row in rows], dtype=np.int8)
    return MetricScores(
        n_matches=len(rows),
        log_loss=log_loss(probabilities, results),
        brier=brier(probabilities, results),
        rps=rps(probabilities, results),
        accuracy=accuracy(probabilities, results),
    )


def _model_key(entry: tuple[tuple[str, str], Sequence[EvaluatedPrediction]]) -> tuple[int, str]:
    """Sort key of a model entry: the report order, then the version."""
    (model, version), _rows = entry
    return (_MODEL_ORDER.get(model, len(MODEL_NAMES)), version)


def _shared_matches(
    by_model: Mapping[tuple[str, str], Sequence[EvaluatedPrediction]],
) -> set[int]:
    """Return the matches every scored model name predicted.

    The set of one name is the union of its versions' matches. :func:`evaluate`
    keeps one scored row per ``(match_id, model)``, so two versions of one model
    occupy disjoint match sets: intersecting per version would empty a
    comparison the names do share. A model name without a scored row contributes
    no set and is therefore excluded, which is what drops the market model when
    it forecast nothing.
    """
    by_name: dict[str, set[int]] = defaultdict(set)
    for (model, _version), rows in by_model.items():
        by_name[model].update(row.match_id for row in rows)
    shared: set[int] | None = None
    for ids in by_name.values():
        shared = set(ids) if shared is None else shared & ids
    return set() if shared is None else shared


def _common(
    by_model: Mapping[tuple[str, str], Sequence[EvaluatedPrediction]],
    shared: set[int],
) -> CommonSubset | None:
    """Return the shared-match metrics, or ``None`` when there are none.

    ``shared`` is the per-model-name intersection of :func:`_shared_matches`, so
    the entries compare model names, never versions. Each entry stays keyed by
    ``(model, model_version)`` and only covers the shared matches: two versions
    of one model are never pooled, and a version with no row in ``shared`` is
    omitted rather than scored on an empty set.
    """
    if not shared:
        return None
    members = tuple(
        ModelMetrics(
            model=model,
            model_version=version,
            scores=_metric_scores(rows),
        )
        for (model, version), all_rows in sorted(by_model.items(), key=_model_key)
        if (rows := [row for row in all_rows if row.match_id in shared])
    )
    return CommonSubset(match_count=len(shared), models=members)


def _market_reference(
    scored: Sequence[EvaluatedPrediction],
    odds_by_match: Mapping[int, NDArray[np.float64]],
    shared: set[int],
) -> MarketReference | None:
    """Return the market benchmark on the shared cohort, where odds exist.

    The reference is scored on exactly the match set ``common`` reports, not on
    a wider one, so the two numbers compare the same matches. The shared matches
    without a usable price are counted in ``matches_without_odds`` rather than
    dropped silently, and the reference is ``None`` when it covers no match.
    """
    results = {row.match_id: row.result for row in scored}
    probabilities: list[NDArray[np.float64]] = []
    outcomes: list[int] = []
    matches_without_odds = 0
    for match_id in sorted(shared):
        odds = odds_by_match.get(match_id)
        if odds is None:
            matches_without_odds += 1
            continue
        prices = np.asarray(odds, dtype=np.float64)
        if prices.shape != (_COLUMNS,):
            raise ValueError(
                f"odds for match {match_id} must have shape ({_COLUMNS},), got {prices.shape}"
            )
        if np.isnan(prices).any():
            if not np.isnan(prices).all():
                raise ValueError(f"odds for match {match_id} mix NaN with real prices")
            matches_without_odds += 1
            continue
        probabilities.append(remove_margin(prices.reshape(1, _COLUMNS))[0])
        outcomes.append(_result_index(match_id, results[match_id]))

    if not probabilities:
        return None
    stacked = np.vstack(probabilities)
    labels = np.array(outcomes, dtype=np.int8)
    return MarketReference(
        model="market",
        model_version=make_model("market", seed=0).version,
        scores=MetricScores(
            n_matches=len(probabilities),
            log_loss=log_loss(stacked, labels),
            brier=brier(stacked, labels),
            rps=rps(stacked, labels),
            accuracy=accuracy(stacked, labels),
        ),
        stored_before_kickoff=False,
        match_count=len(probabilities),
        matches_without_odds=matches_without_odds,
    )


def _counts(evaluated: Sequence[EvaluatedPrediction]) -> PredictionCounts:
    """Return the per-outcome counts of every evaluated row."""

    def count(outcome: PredictionOutcome) -> int:
        return sum(1 for row in evaluated if row.outcome == outcome)

    return PredictionCounts(
        n_rows=len(evaluated),
        n_scored=count(_SCORED),
        n_pending=count(_PENDING),
        n_superseded=count(_SUPERSEDED),
        n_late=count(_LATE),
        n_withdrawn=count(_WITHDRAWN),
    )
