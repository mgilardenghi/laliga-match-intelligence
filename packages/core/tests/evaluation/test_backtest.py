"""Tests for the backtest runner and the data fingerprint.

The expectations are hand computed from the synthetic league: the fixture
counts come from its seasons, the metric cross-check rebuilds the same rows with
the walk-forward API, and the ordering is the documented one.
"""

import numpy as np
import pytest
from lmi_core.evaluation.backtest import data_fingerprint, run_backtest
from lmi_core.evaluation.metrics import accuracy, brier, log_loss, rps
from lmi_core.evaluation.report import BacktestConfig, BacktestReport, ScoreRow, Subset
from lmi_core.evaluation.walkforward import walk_forward, weekly_cutoffs
from modeling.leakage_harness import (
    make_history,
    make_synthetic_league,
    perturb_results_from,
    to_date,
)

MODELS = ("home_win", "base_rate")
ORDERED_MODELS = ("home_win", "base_rate")
SUBSETS: tuple[Subset, Subset] = ("all", "common")
SEASONS = (1, 2, 3)


def test_scores_are_emitted_in_the_documented_order() -> None:
    data = make_synthetic_league()

    report = run_backtest(data, _config(models=("base_rate", "home_win")))

    expected: list[tuple[str, int | None, Subset]] = [
        (model, season, subset)
        for model in ORDERED_MODELS
        for season in (*SEASONS, None)
        for subset in SUBSETS
    ]
    assert [(row.model, row.season_id, row.subset) for row in report.scores] == expected


def test_coverage_counts_follow_the_scope() -> None:
    data = make_synthetic_league()

    report = run_backtest(data, _config())

    fixtures_by_season = {
        season: int(np.count_nonzero(data.season_id == season)) for season in SEASONS
    }
    total = len(data)
    for row in report.scores:
        expected_fixtures = total if row.season_id is None else fixtures_by_season[row.season_id]
        assert row.n_fixtures == expected_fixtures
        assert row.n_matches <= row.n_fixtures
    assert _row(report, "base_rate", None, "all").n_matches == total
    assert _row(report, "base_rate", None, "all").n_fixtures == total
    for model in ORDERED_MODELS:
        for season in (*SEASONS, None):
            assert (
                _row(report, model, season, "common").n_matches
                <= _row(report, model, season, "all").n_matches
            )


def test_a_row_matches_the_metrics_of_the_reference_walk_forward() -> None:
    data = make_synthetic_league()
    config = _config()

    report = run_backtest(data, config)

    cutoffs = weekly_cutoffs(to_date(data.kickoff_date.min()), to_date(data.kickoff_date.max()))
    predictions = walk_forward(
        data,
        "base_rate",
        seed=config.seed,
        cutoffs=cutoffs,
        step_days=config.step_days,
    )
    season = 2
    mask = (predictions.season_id == season) & ~np.isnan(predictions.probabilities).all(axis=1)
    probabilities = predictions.probabilities[mask]
    result = predictions.result[mask]
    row = _row(report, "base_rate", season, "all")

    assert row.n_matches == int(np.count_nonzero(mask))
    assert row.log_loss == log_loss(probabilities, result)
    assert row.brier == brier(probabilities, result)
    assert row.rps == rps(probabilities, result)
    assert row.accuracy == accuracy(probabilities, result)


def test_from_year_moves_the_scored_range() -> None:
    data = make_synthetic_league()

    two_seasons = run_backtest(data, _config(from_year=2006))
    default = run_backtest(data, BacktestConfig(models=MODELS))

    assert {row.season_id for row in two_seasons.scores if row.season_id is not None} == {
        2,
        3,
    }
    assert {row.season_id for row in default.scores if row.season_id is not None} == {3}


def test_calibration_covers_each_model_over_the_scored_range() -> None:
    data = make_synthetic_league()

    report = run_backtest(data, _config())

    assert [row.model for row in report.calibration] == [
        model for model in ORDERED_MODELS for _ in range(30)
    ]
    for model in ORDERED_MODELS:
        rows = [row for row in report.calibration if row.model == model]
        assert len(rows) == 30
        assert len({(row.outcome, row.bin_index) for row in rows}) == 30
        overall = _row(report, model, None, "all")
        for outcome in ("H", "D", "A"):
            assert sum(row.n for row in rows if row.outcome == outcome) == overall.n_matches


def test_backtest_is_deterministic_and_fingerprints_the_input() -> None:
    data = make_synthetic_league()

    first = run_backtest(data, _config())
    second = run_backtest(data, _config())

    assert first.scores == second.scores
    assert first.calibration == second.calibration
    assert first.data_fingerprint == second.data_fingerprint
    unchanged = data.select(np.ones(len(data), dtype=np.bool_))
    assert data_fingerprint(unchanged) == data_fingerprint(data)
    changed = perturb_results_from(data, to_date(data.kickoff_date[0]))
    assert data_fingerprint(changed) != data_fingerprint(data)


def test_the_fingerprint_accepts_rows_without_odds() -> None:
    history = make_history([0, 1, 2])

    digest = data_fingerprint(history)

    assert digest == data_fingerprint(make_history([0, 1, 2]))
    assert len(digest) == 64
    assert int(digest, 16) >= 0


def test_invalid_configs_raise() -> None:
    data = make_synthetic_league()

    with pytest.raises(ValueError, match="unknown model"):
        run_backtest(data, BacktestConfig(models=("poisson",)))
    with pytest.raises(ValueError, match="must not be empty"):
        run_backtest(data, BacktestConfig(models=()))
    with pytest.raises(ValueError, match="step_days"):
        run_backtest(data, BacktestConfig(models=MODELS, step_days=0))
    with pytest.raises(ValueError, match="from_year"):
        run_backtest(data, BacktestConfig(models=MODELS, from_year=2100))


def _config(
    *,
    models: tuple[str, ...] = MODELS,
    from_year: int = 2005,
    step_days: int = 7,
) -> BacktestConfig:
    return BacktestConfig(models=models, from_year=from_year, step_days=step_days)


def _row(report: BacktestReport, model: str, season: int | None, subset: Subset) -> ScoreRow:
    rows = [
        row
        for row in report.scores
        if row.model == model and row.season_id == season and row.subset == subset
    ]
    assert len(rows) == 1
    return rows[0]
