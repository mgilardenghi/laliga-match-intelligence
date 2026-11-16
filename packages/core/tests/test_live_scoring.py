"""Unit tests for the live scoring rules and the live metrics report.

Everything here builds :class:`~lmi_core.live.schemas.PredictionRow` values by
hand: no database, no clock and no model. The dates and probabilities are
synthetic.
"""

import math
from datetime import UTC, date, datetime
from typing import Literal

import numpy as np
import pytest
from lmi_core.evaluation.metrics import accuracy, brier, log_loss, rps
from lmi_core.live.schemas import EvaluatedPrediction, PredictionRow
from lmi_core.live.scoring import evaluate, live_metrics
from lmi_core.modeling.market import remove_margin
from lmi_core.modeling.registry import make_model

MatchStatus = Literal["scheduled", "played", "withdrawn"]
Result = Literal["H", "D", "A"]

_DEFAULT_CREATED = datetime(2026, 5, 16, 8, tzinfo=UTC)
_DEFAULT_AS_OF = date(2026, 5, 16)
_KICKOFF = date(2026, 5, 20)


def _row(
    *,
    match_id: int = 1,
    model: str = "home_win",
    model_version: str = "test-1",
    as_of_date: date = _DEFAULT_AS_OF,
    created_at: datetime = _DEFAULT_CREATED,
    probabilities: tuple[float, float, float] = (0.5, 0.3, 0.2),
    kickoff_date: date = _KICKOFF,
    match_status: MatchStatus = "scheduled",
    result: Result | None = None,
) -> PredictionRow:
    """Build one stored prediction row with defaults for a scheduled fixture."""
    return PredictionRow(
        match_id=match_id,
        model=model,
        model_version=model_version,
        as_of_date=as_of_date,
        created_at=created_at,
        p_home=probabilities[0],
        p_draw=probabilities[1],
        p_away=probabilities[2],
        kickoff_date=kickoff_date,
        match_status=match_status,
        result=result,
    )


def _by_as_of(evaluated: list[EvaluatedPrediction]) -> dict[date, EvaluatedPrediction]:
    """Map each evaluated row to its as-of date."""
    return {row.as_of_date: row for row in evaluated}


def test_l10_the_latest_valid_row_of_a_played_match_is_scored() -> None:
    rows = [
        _row(
            as_of_date=date(2026, 5, 13),
            created_at=datetime(2026, 5, 13, 8, tzinfo=UTC),
            match_status="played",
            result="H",
        ),
        _row(match_status="played", result="H"),
    ]

    by_as_of = _by_as_of(evaluate(rows))

    later = by_as_of[date(2026, 5, 16)]
    earlier = by_as_of[date(2026, 5, 13)]
    assert later.outcome == "scored"
    assert later.log_loss is not None
    assert earlier.outcome == "superseded"
    assert earlier.log_loss is None
    assert earlier.brier is None


def test_l10_a_match_moved_earlier_makes_an_on_time_row_late() -> None:
    # The match was dated 2026-05-20 when the row was stored and its current
    # date is 2026-05-15, so the row stamped on 2026-05-16 is late now.
    rows = [
        _row(
            as_of_date=date(2026, 5, 13),
            created_at=datetime(2026, 5, 13, 8, tzinfo=UTC),
            kickoff_date=date(2026, 5, 15),
            match_status="played",
            result="A",
        ),
        _row(kickoff_date=date(2026, 5, 15), match_status="played", result="A"),
    ]

    by_as_of = _by_as_of(evaluate(rows))

    assert by_as_of[date(2026, 5, 16)].outcome == "late"
    assert by_as_of[date(2026, 5, 16)].log_loss is None
    assert by_as_of[date(2026, 5, 13)].outcome == "scored"


def test_l10_withdrawn_rows_are_never_scored() -> None:
    rows = [
        _row(
            as_of_date=date(2026, 5, 13),
            created_at=datetime(2026, 5, 13, 8, tzinfo=UTC),
            match_status="withdrawn",
        ),
        _row(match_status="withdrawn"),
    ]

    evaluated = evaluate(rows)

    assert [row.outcome for row in evaluated] == ["withdrawn", "withdrawn"]
    assert all(row.log_loss is None and row.correct is None for row in evaluated)


def test_same_as_of_date_scores_the_later_creation_instant() -> None:
    rows = [
        _row(created_at=datetime(2026, 5, 16, 8, tzinfo=UTC), match_status="played", result="H"),
        _row(created_at=datetime(2026, 5, 16, 20, tzinfo=UTC), match_status="played", result="H"),
    ]

    evaluated = evaluate(rows)

    assert [(row.created_at.hour, row.outcome) for row in evaluated] == [
        (8, "superseded"),
        (20, "scored"),
    ]


def test_hand_computed_scores_match_the_metric_functions() -> None:
    evaluated = evaluate([_row(match_status="played", result="H")])
    scored = evaluated[0]

    assert scored.outcome == "scored"
    probabilities = np.array([[0.5, 0.3, 0.2]], dtype=np.float64)
    results = np.array([0], dtype=np.int8)

    assert scored.log_loss == pytest.approx(-math.log(0.5))
    assert scored.brier == pytest.approx(0.25 + 0.09 + 0.04)
    assert scored.rps == pytest.approx(((0.5 - 1.0) ** 2 + (0.8 - 1.0) ** 2) / 2.0)
    assert scored.correct is True

    assert scored.log_loss == log_loss(probabilities, results)
    assert scored.brier == brier(probabilities, results)
    assert scored.rps == rps(probabilities, results)
    assert scored.correct == bool(accuracy(probabilities, results))


def test_evaluate_returns_the_documented_order() -> None:
    rows = [
        _row(match_id=2, model="base_rate", match_status="played", result="D"),
        _row(match_id=1, model="home_win", match_status="played", result="H"),
        _row(match_id=1, model="base_rate", match_status="played", result="H"),
    ]

    evaluated = evaluate(rows)

    assert [(row.match_id, row.model) for row in evaluated] == [
        (1, "base_rate"),
        (1, "home_win"),
        (2, "base_rate"),
    ]


def test_a_played_row_without_a_result_raises() -> None:
    with pytest.raises(ValueError, match="match 7"):
        evaluate([_row(match_id=7, match_status="played", result=None)])


def _scored_rows() -> list[PredictionRow]:
    """Two played matches, the second predicted by one model only."""
    return [
        _row(match_id=1, model="home_win", match_status="played", result="H"),
        _row(match_id=1, model="base_rate", match_status="played", result="H"),
        _row(
            match_id=2,
            model="home_win",
            probabilities=(0.2, 0.3, 0.5),
            match_status="played",
            result="A",
        ),
    ]


def test_live_metrics_aggregates_per_model_and_on_the_common_subset() -> None:
    evaluated = evaluate(_scored_rows())
    today = date(2026, 5, 25)

    metrics = live_metrics(evaluated, {}, today=today)

    assert metrics.evaluated_as_of == today
    assert [(entry.model, entry.scores.n_matches) for entry in metrics.models] == [
        ("home_win", 2),
        ("base_rate", 1),
    ]
    probabilities = np.array([[0.5, 0.3, 0.2], [0.2, 0.3, 0.5]], dtype=np.float64)
    results = np.array([0, 2], dtype=np.int8)
    home_win = metrics.models[0].scores
    assert home_win.log_loss == log_loss(probabilities, results)
    assert home_win.brier == brier(probabilities, results)
    assert home_win.rps == rps(probabilities, results)
    assert home_win.accuracy == accuracy(probabilities, results)

    assert metrics.common is not None
    assert metrics.common.match_count == 1
    assert [entry.model for entry in metrics.common.models] == ["home_win", "base_rate"]
    assert [entry.scores.n_matches for entry in metrics.common.models] == [1, 1]
    common_probabilities = np.array([[0.5, 0.3, 0.2]], dtype=np.float64)
    single = np.array([0], dtype=np.int8)
    assert metrics.common.models[0].scores.log_loss == log_loss(common_probabilities, single)

    assert metrics.market_reference is None
    assert metrics.counts.n_rows == 3
    assert metrics.counts.n_scored == 3
    assert metrics.counts.n_pending == 0
    assert metrics.counts.n_superseded == 0
    assert metrics.counts.n_late == 0
    assert metrics.counts.n_withdrawn == 0


def test_live_metrics_market_reference_uses_the_stored_odds() -> None:
    evaluated = evaluate(_scored_rows())
    odds = {
        1: np.array([2.0, 3.5, 4.0], dtype=np.float64),
        2: np.array([np.nan, np.nan, np.nan], dtype=np.float64),
    }

    metrics = live_metrics(evaluated, odds, today=date(2026, 5, 25))

    reference = metrics.market_reference
    assert reference is not None
    assert reference.model == "market"
    assert reference.model_version == make_model("market", seed=0).version
    assert reference.stored_before_kickoff is False
    assert reference.match_count == 1
    # The match 2 odds are all NaN and match 2 is outside the shared cohort, so
    # it is not part of the reference at all.
    assert reference.matches_without_odds == 0

    probabilities = remove_margin(np.array([[2.0, 3.5, 4.0]], dtype=np.float64))
    results = np.array([0], dtype=np.int8)
    assert reference.scores.n_matches == 1
    assert reference.scores.log_loss == log_loss(probabilities, results)
    assert reference.scores.brier == brier(probabilities, results)
    assert reference.scores.rps == rps(probabilities, results)
    assert reference.scores.accuracy == accuracy(probabilities, results)


def test_live_metrics_compares_model_names_when_one_has_two_versions() -> None:
    rows = [
        _row(match_id=1, model="home_win", model_version="v1", match_status="played", result="H"),
        _row(match_id=2, model="home_win", model_version="v2", match_status="played", result="D"),
        _row(match_id=1, model="base_rate", match_status="played", result="H"),
        _row(match_id=2, model="base_rate", match_status="played", result="D"),
    ]

    metrics = live_metrics(evaluate(rows), {}, today=date(2026, 5, 25))

    # Two versions of one model occupy disjoint match sets, but the name covers
    # both matches, so the shared set is the name intersection, not the empty
    # intersection over versions.
    assert metrics.common is not None
    assert metrics.common.match_count == 2
    assert [
        (entry.model, entry.model_version, entry.scores.n_matches)
        for entry in metrics.common.models
    ] == [("home_win", "v1", 1), ("home_win", "v2", 1), ("base_rate", "test-1", 2)]

    # A version with no row in the shared set is omitted instead of pooling with
    # its sibling or being scored on an empty set.
    outside = [
        _row(match_id=1, model="home_win", model_version="v1", match_status="played", result="H"),
        _row(match_id=3, model="home_win", model_version="v2", match_status="played", result="A"),
        _row(match_id=1, model="base_rate", match_status="played", result="H"),
        _row(match_id=2, model="base_rate", match_status="played", result="D"),
    ]

    partial = live_metrics(evaluate(outside), {}, today=date(2026, 5, 25))

    assert partial.common is not None
    assert partial.common.match_count == 1
    assert [
        (entry.model, entry.model_version, entry.scores.n_matches)
        for entry in partial.common.models
    ] == [("home_win", "v1", 1), ("base_rate", "test-1", 1)]


def test_the_market_reference_is_scored_on_the_common_cohort() -> None:
    # home_win scored matches {1, 2} and base_rate scored {1}, so the shared
    # cohort is {1} even though match 2 carries odds.
    evaluated = evaluate(_scored_rows())
    odds = {
        1: np.array([2.0, 3.5, 4.0], dtype=np.float64),
        2: np.array([1.8, 3.6, 4.5], dtype=np.float64),
    }

    metrics = live_metrics(evaluated, odds, today=date(2026, 5, 25))

    reference = metrics.market_reference
    assert reference is not None
    assert metrics.common is not None
    assert metrics.common.match_count == 1
    assert reference.match_count == reference.scores.n_matches == 1
    # Match 2 has odds and is outside the cohort, so it is neither scored nor
    # counted as missing a price.
    assert reference.matches_without_odds == 0
    probabilities = remove_margin(np.array([[2.0, 3.5, 4.0]], dtype=np.float64))
    results = np.array([0], dtype=np.int8)
    assert reference.scores.log_loss == log_loss(probabilities, results)

    # A shared match without a usable price is counted there, not dropped.
    both_matches = [
        _row(match_id=1, model="home_win", match_status="played", result="H"),
        _row(match_id=2, model="home_win", match_status="played", result="D"),
        _row(match_id=1, model="base_rate", match_status="played", result="H"),
        _row(match_id=2, model="base_rate", match_status="played", result="D"),
    ]
    partial_odds = {
        1: np.array([2.0, 3.5, 4.0], dtype=np.float64),
        2: np.array([np.nan, np.nan, np.nan], dtype=np.float64),
    }

    partial = live_metrics(evaluate(both_matches), partial_odds, today=date(2026, 5, 25))

    assert partial.common is not None
    assert partial.common.match_count == 2
    assert partial.market_reference is not None
    assert partial.market_reference.match_count == 1
    assert partial.market_reference.matches_without_odds == 1


def test_live_metrics_without_scored_rows_reports_only_counts() -> None:
    evaluated = evaluate([_row(match_id=3)])
    today = date(2026, 5, 16)

    metrics = live_metrics(evaluated, {3: np.array([2.0, 3.5, 4.0], dtype=np.float64)}, today=today)

    assert metrics.models == ()
    assert metrics.common is None
    assert metrics.market_reference is None
    assert metrics.counts.n_rows == 1
    assert metrics.counts.n_pending == 1
