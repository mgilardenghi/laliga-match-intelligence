"""The walk-forward backtest runner and the fingerprint of its input data.

The runner scores every model on the seasons that start in or after
``config.from_year``. Seasons are derived from the data itself -- the calendar
year of a season's earliest kickoff -- because the evaluation package must not
import the database. The warm-up seasons before that boundary stay in every fit
history, so the first scored window already has played matches behind it.

Rows are emitted in a fixed order and the input is hashed, so two runs with the
same data and configuration produce the same report.
"""

import datetime
import hashlib

import numpy as np
from numpy.typing import NDArray

from lmi_core.evaluation.metrics import accuracy, brier, calibration_bins, log_loss, rps
from lmi_core.evaluation.report import (
    BacktestConfig,
    BacktestReport,
    CalibrationRow,
    ScoreRow,
    Subset,
)
from lmi_core.evaluation.walkforward import WindowPredictions, walk_forward, weekly_cutoffs
from lmi_core.modeling.registry import MODEL_NAMES, make_model
from lmi_core.modeling.types import MatchData

_ODDS_DECIMALS = 3
_MISSING_ODDS = -1.0


def data_fingerprint(data: MatchData) -> str:
    """Return the sha256 of the canonical bytes of every row and column.

    Rows are sorted by ``(kickoff_date, match_id)`` so the digest does not
    depend on the input order. Integer columns contribute 8-byte little-endian
    signed integers, the kickoff date counts days since the epoch, and the odds
    are rounded to three decimals row-major with a missing value written as
    ``-1``.
    """
    order = np.lexsort((data.match_id, data.kickoff_date))
    digest = hashlib.sha256()
    for column in (
        data.match_id,
        data.kickoff_date.astype("datetime64[D]").astype(np.int64),
        data.home_team_id,
        data.away_team_id,
        data.home_goals,
        data.away_goals,
    ):
        digest.update(np.asarray(column[order], dtype="<i8").tobytes())
    odds = np.round(data.odds[order], _ODDS_DECIMALS)
    canonical_odds = np.where(np.isnan(odds), _MISSING_ODDS, odds)
    digest.update(np.asarray(canonical_odds, dtype="<f8").tobytes())
    return digest.hexdigest()


def run_backtest(data: MatchData, config: BacktestConfig) -> BacktestReport:
    """Run a walk-forward backtest over the scored seasons and report its rows.

    Raises:
        ValueError: the configuration is invalid, no season starts in or after
            ``config.from_year``, a model forecast no scored match, or the
            models forecast no match in common.
    """
    models = _validate_config(config)
    season_years = _season_start_years(data)
    scored_seasons = _scored_seasons(season_years, config.from_year)
    scored_array = np.array(scored_seasons, dtype=np.int64)
    scored_rows = np.isin(data.season_id, scored_array)
    if not bool(scored_rows.any()):
        raise ValueError(
            f"no played match belongs to a season starting in or after from_year={config.from_year}"
        )

    fixtures_by_season = {
        season: int(np.count_nonzero(data.season_id == season)) for season in scored_seasons
    }
    total_fixtures = sum(fixtures_by_season.values())
    scored_kickoffs = data.kickoff_date[scored_rows]
    cutoffs = weekly_cutoffs(
        _as_date(scored_kickoffs.min()),
        _as_date(scored_kickoffs.max()),
        config.step_days,
    )

    predictions = {
        name: _scored_predictions(
            walk_forward(
                data,
                name,
                seed=config.seed,
                cutoffs=cutoffs,
                step_days=config.step_days,
            ),
            scored_array,
        )
        for name in models
    }

    forecast_masks: dict[str, NDArray[np.bool_]] = {}
    for name in models:
        mask = _forecast_mask(predictions[name])
        if not bool(mask.any()):
            raise ValueError(f"{name}: the model forecast no match in the scored range")
        forecast_masks[name] = mask

    common_ids = predictions[models[0]].match_id[forecast_masks[models[0]]]
    for name in models[1:]:
        available = predictions[name].match_id[forecast_masks[name]]
        common_ids = common_ids[np.isin(common_ids, available)]
    if common_ids.size == 0:
        raise ValueError("the models have no match in common to compare")
    common_masks = {name: np.isin(predictions[name].match_id, common_ids) for name in models}

    scores: list[ScoreRow] = []
    for name in models:
        model_predictions = predictions[name]
        all_mask = forecast_masks[name]
        common_mask = common_masks[name]
        for season in [*scored_seasons, None]:
            if season is None:
                scope = np.ones(len(model_predictions), dtype=np.bool_)
                n_fixtures = total_fixtures
            else:
                scope = model_predictions.season_id == season
                n_fixtures = fixtures_by_season[season]
            subsets: tuple[tuple[Subset, NDArray[np.bool_]], ...] = (
                ("all", all_mask),
                ("common", common_mask),
            )
            for subset, subset_mask in subsets:
                selected = scope & subset_mask
                n_matches = int(np.count_nonzero(selected))
                if n_matches == 0:
                    continue
                probabilities = model_predictions.probabilities[selected]
                result = model_predictions.result[selected]
                scores.append(
                    ScoreRow(
                        model=name,
                        season_id=season,
                        subset=subset,
                        n_matches=n_matches,
                        n_fixtures=n_fixtures,
                        log_loss=log_loss(probabilities, result),
                        brier=brier(probabilities, result),
                        rps=rps(probabilities, result),
                        accuracy=accuracy(probabilities, result),
                    )
                )

    calibration: list[CalibrationRow] = []
    for name in models:
        mask = forecast_masks[name]
        calibration.extend(
            calibration_bins(
                predictions[name].probabilities[mask],
                predictions[name].result[mask],
                model=name,
            )
        )

    descriptions = {name: make_model(name, seed=config.seed).describe() for name in models}
    return BacktestReport(
        config=config,
        scores=scores,
        calibration=calibration,
        data_fingerprint=data_fingerprint(data),
        model_descriptions=descriptions,
    )


def _validate_config(config: BacktestConfig) -> tuple[str, ...]:
    """Check the configuration and return its models in report order."""
    if not config.models:
        raise ValueError("models must not be empty")
    for name in config.models:
        if name not in MODEL_NAMES:
            known = ", ".join(MODEL_NAMES)
            raise ValueError(f"unknown model {name!r}; known models are {known}")
    if config.step_days < 1:
        raise ValueError(f"step_days must be at least 1, got {config.step_days}")
    return tuple(name for name in MODEL_NAMES if name in config.models)


def _season_start_years(data: MatchData) -> dict[int, int]:
    """Return the calendar year of the earliest kickoff of every season."""
    years: dict[int, int] = {}
    for season in np.unique(data.season_id):
        season_id = int(season)
        earliest = data.kickoff_date[data.season_id == season_id].min()
        years[season_id] = _as_date(earliest).year
    return years


def _scored_seasons(season_years: dict[int, int], from_year: int) -> list[int]:
    """Return the seasons starting in or after ``from_year``, oldest first."""
    seasons = sorted(
        (season for season, year in season_years.items() if year >= from_year),
        key=lambda season: (season_years[season], season),
    )
    if not seasons:
        raise ValueError(f"no season starts in or after from_year={from_year}")
    return seasons


def _scored_predictions(
    predictions: WindowPredictions, scored_seasons: NDArray[np.int64]
) -> WindowPredictions:
    """Keep the walk-forward rows that belong to a scored season."""
    keep = np.isin(predictions.season_id, scored_seasons)
    return WindowPredictions(
        match_id=predictions.match_id[keep],
        probabilities=predictions.probabilities[keep],
        result=predictions.result[keep],
        season_id=predictions.season_id[keep],
    )


def _forecast_mask(predictions: WindowPredictions) -> NDArray[np.bool_]:
    """Return the rows that carry at least one real probability."""
    return ~np.isnan(predictions.probabilities).all(axis=1)


def _as_date(value: np.datetime64) -> datetime.date:
    """Convert a NumPy date to a :class:`datetime.date`."""
    converted = value.astype("datetime64[D]").item()
    if not isinstance(converted, datetime.date):
        raise TypeError(f"expected a date, got {value!r}")
    return converted
