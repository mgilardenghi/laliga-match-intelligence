"""Append-only persistence for walk-forward backtest results.

The worker writes a run in three steps: :func:`start_run` inserts a ``running``
row and commits it, :func:`finish_run` stores the metrics and the calibration
bins and marks the run ``succeeded``, or :func:`fail_run` marks it ``failed``.
The API reads a stored run through :func:`load_report`.

Runs are append-only. The only update is the status transition
``running -> succeeded | failed`` plus its ``finished_at``, and on success the
``data_fingerprint`` and ``n_matches`` of the run. Metric and calibration rows
are written once and never changed.

Sessions are synchronous SQLAlchemy 2 sessions (psycopg 3), the shape
:mod:`lmi_core.db.session` builds for the API and the worker. Every function
commits its own transaction and leaves the session usable.
"""

import datetime
import importlib.metadata
import os
import uuid
from collections.abc import Sequence
from typing import Final, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from lmi_core.db.models import BacktestCalibration, BacktestMetric, BacktestRun, Season
from lmi_core.results.schemas import (
    BACKTEST_MODEL_NAMES,
    BacktestRunOut,
    CalibrationBin,
    MetricRow,
    ModelInfo,
    ModelMetricsResponse,
    ModelName,
    Outcome,
    Subset,
)

GIT_SHA_VARIABLE = "GIT_SHA"
"""The optional build variable whose value is appended to the package version."""

_MODEL_ORDER: Final[dict[str, int]] = {
    name: index for index, name in enumerate(BACKTEST_MODEL_NAMES)
}
"""Report position of each model name; unknown names sort last."""

_SUBSET_ORDER: Final[dict[str, int]] = {"all": 0, "common": 1}
"""Report position of each subset: ``all`` before ``common``."""

_OUTCOME_ORDER: Final[dict[str, int]] = {"H": 0, "D": 1, "A": 2}
"""Report position of each outcome, matching the model output order."""


def installed_code_version() -> str:
    """Return the installed ``lmi-core`` version, tagged with ``GIT_SHA`` if set.

    The environment variable carries the commit a container image was built
    from. The code never shells out to git: the runtime image may not contain a
    repository, and a failing subprocess must not break a backtest.
    """
    version = importlib.metadata.version("lmi-core")
    sha = os.environ.get(GIT_SHA_VARIABLE, "").strip()
    return f"{version}+{sha}" if sha else version


def start_run(
    session: Session,
    *,
    seed: int,
    from_year: int,
    step_days: int,
    models: Sequence[ModelInfo],
    code_version: str | None = None,
) -> uuid.UUID:
    """Insert a ``running`` run row and commit it.

    The models and their ``describe()`` version and hyperparameters are stored
    in the run's ``config`` column, so a later reader knows which code produced
    the metrics.

    Args:
        session: the session that writes the row.
        seed: the seed passed to every model of the run.
        from_year: the first scored season, as a start year.
        step_days: the length of a walk-forward window in days.
        models: the models the run scores.
        code_version: the version to record. ``None`` reads the installed
            package version through :func:`installed_code_version`.

    Returns:
        The id the database generated for the run.
    """
    run = BacktestRun(
        status="running",
        seed=seed,
        from_year=from_year,
        step_days=step_days,
        code_version=installed_code_version() if code_version is None else code_version,
        config={"models": [model.model_dump(mode="json") for model in models]},
    )
    session.add(run)
    session.flush()
    run_id = run.id
    _commit(session)
    return run_id


def finish_run(
    session: Session,
    run_id: uuid.UUID,
    *,
    metrics: Sequence[MetricRow],
    calibration: Sequence[CalibrationBin],
    data_fingerprint: str,
    n_matches: int,
) -> None:
    """Store the results of a running run and mark it ``succeeded``.

    Every metric and calibration row, the status, ``finished_at``,
    ``data_fingerprint`` and ``n_matches`` are written in one transaction, so a
    reader never sees a succeeded run with a partial result set.

    ``MetricRow.coverage`` is not stored: it is derived from ``n_matches`` and
    ``n_fixtures`` when a report is loaded.

    Raises:
        ValueError: the run does not exist, is no longer ``running``, or a
            metric row names a season code that the ``seasons`` table does not
            contain. Nothing is written in any of these cases.
    """
    run = _running_run(session, run_id)
    seasons = _resolve_seasons(session, metrics)
    session.add_all([_metric_model(run, row, seasons) for row in metrics])
    session.add_all([_calibration_model(run, row) for row in calibration])
    run.status = "succeeded"
    run.finished_at = datetime.datetime.now(datetime.UTC)
    run.data_fingerprint = data_fingerprint
    run.n_matches = n_matches
    _commit(session)


def fail_run(session: Session, run_id: uuid.UUID, *, error: str) -> None:
    """Mark a running run ``failed`` and record why.

    Raises:
        ValueError: the run does not exist or is no longer ``running``. The row
            is left untouched in both cases.
    """
    run = _running_run(session, run_id)
    run.status = "failed"
    run.finished_at = datetime.datetime.now(datetime.UTC)
    run.error = error
    _commit(session)


def load_report(session: Session, run_id: uuid.UUID | None = None) -> ModelMetricsResponse | None:
    """Return one succeeded run as the API serves it, or ``None``.

    Args:
        session: the session that reads the rows.
        run_id: the run to load. ``None`` loads the latest succeeded run by
            ``started_at``, with the run id as a deterministic tie-break.

    Returns:
        The report for a succeeded run, or ``None`` when the requested run does
        not exist, is still ``running``, has ``failed``, or when no succeeded
        run exists. A run that is not succeeded is never returned.
    """
    statement = select(BacktestRun).where(BacktestRun.status == "succeeded")
    if run_id is not None:
        statement = statement.where(BacktestRun.id == run_id)
    statement = statement.order_by(BacktestRun.started_at.desc(), BacktestRun.id.desc()).limit(1)
    run = session.scalars(statement).one_or_none()
    if run is None:
        return None

    if run.data_fingerprint is None or run.n_matches is None:
        raise ValueError(
            f"backtest run {run.id} is succeeded but has no data fingerprint or match count"
        )

    seasons = {season.id: season for season in session.scalars(select(Season))}
    return ModelMetricsResponse(
        run=BacktestRunOut(
            id=run.id,
            started_at=run.started_at,
            finished_at=run.finished_at,
            seed=run.seed,
            from_year=run.from_year,
            step_days=run.step_days,
            code_version=run.code_version,
            data_fingerprint=run.data_fingerprint,
            n_matches=run.n_matches,
        ),
        models=_models(run),
        metrics=_metrics(session, run, seasons),
        calibration=_calibration(session, run),
    )


def _commit(session: Session) -> None:
    """Commit ``session``, rolling back a failed transaction before re-raising."""
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise


def _running_run(session: Session, run_id: uuid.UUID) -> BacktestRun:
    """Return the run ``run_id`` if it is still running.

    Raises:
        ValueError: the run does not exist or has already finished.
    """
    run = session.get(BacktestRun, run_id)
    if run is None:
        raise ValueError(f"backtest run {run_id} does not exist")
    if run.status != "running":
        raise ValueError(f"backtest run {run_id} is {run.status!r}, not 'running'")
    return run


def _resolve_seasons(session: Session, metrics: Sequence[MetricRow]) -> dict[str, int]:
    """Map every season code a metric row names to its ``seasons.id``.

    Raises:
        ValueError: a code has no row in ``seasons``. The message names every
            unknown code and never inserts a partial metric set.
    """
    codes = sorted({row.season_code for row in metrics if row.season_code is not None})
    if not codes:
        return {}
    stored = {
        season.code: season.id
        for season in session.scalars(select(Season).where(Season.code.in_(codes)))
    }
    unknown = sorted(set(codes) - set(stored))
    if unknown:
        raise ValueError(
            "unknown season code(s): "
            + ", ".join(unknown)
            + "; seed the seasons before storing a backtest"
        )
    return stored


def _metric_model(run: BacktestRun, row: MetricRow, seasons: dict[str, int]) -> BacktestMetric:
    """Build the stored metric row of one DTO row."""
    return BacktestMetric(
        run_id=run.id,
        model=row.model,
        season_id=None if row.season_code is None else seasons[row.season_code],
        subset=row.subset,
        n_matches=row.n_matches,
        n_fixtures=row.n_fixtures,
        log_loss=row.log_loss,
        brier=row.brier,
        rps=row.rps,
        accuracy=row.accuracy,
    )


def _calibration_model(run: BacktestRun, row: CalibrationBin) -> BacktestCalibration:
    """Build the stored calibration row of one DTO row."""
    return BacktestCalibration(
        run_id=run.id,
        model=row.model,
        outcome=row.outcome,
        bin_index=row.bin_index,
        bin_lower=row.bin_lower,
        bin_upper=row.bin_upper,
        n=row.n,
        mean_predicted=row.mean_predicted,
        observed_frequency=row.observed_frequency,
    )


def _models(run: BacktestRun) -> list[ModelInfo]:
    """Return the models of ``run`` in report order, read from its config.

    Raises:
        ValueError: the config has no models list.
        pydantic.ValidationError: a stored model does not match the DTO.
    """
    raw = run.config.get("models")
    if not isinstance(raw, list):
        raise ValueError(f"backtest run {run.id} has no models list in its config")
    models = [ModelInfo.model_validate(item) for item in raw]
    models.sort(key=lambda info: _model_order(info.name))
    return models


def _metrics(session: Session, run: BacktestRun, seasons: dict[int, Season]) -> list[MetricRow]:
    """Return the metric rows of ``run`` in the contract order.

    The order is the model report order, then the season start year ascending
    with the overall row last, then ``all`` before ``common``.

    Raises:
        ValueError: a metric row references a season id that does not exist.
    """
    rows: list[tuple[BacktestMetric, Season | None]] = []
    for row in session.scalars(select(BacktestMetric).where(BacktestMetric.run_id == run.id)):
        season: Season | None = None
        if row.season_id is not None:
            season = seasons.get(row.season_id)
            if season is None:
                raise ValueError(
                    f"backtest metric {row.id} references unknown season id {row.season_id}"
                )
        rows.append((row, season))
    rows.sort(
        key=lambda item: (
            _model_order(item[0].model),
            item[1] is None,
            0 if item[1] is None else item[1].start_year,
            _SUBSET_ORDER.get(item[0].subset, len(_SUBSET_ORDER)),
        )
    )
    return [_metric_row(row, season) for row, season in rows]


def _metric_row(row: BacktestMetric, season: Season | None) -> MetricRow:
    """Build the DTO row of one stored metric row."""
    return MetricRow(
        model=cast(ModelName, row.model),
        season_code=None if season is None else season.code,
        subset=cast(Subset, row.subset),
        n_matches=row.n_matches,
        n_fixtures=row.n_fixtures,
        coverage=row.n_matches / row.n_fixtures,
        log_loss=row.log_loss,
        brier=row.brier,
        rps=row.rps,
        accuracy=row.accuracy,
    )


def _calibration(session: Session, run: BacktestRun) -> list[CalibrationBin]:
    """Return the calibration bins of ``run`` in model, outcome and bin order."""
    rows = list(
        session.scalars(select(BacktestCalibration).where(BacktestCalibration.run_id == run.id))
    )
    rows.sort(
        key=lambda row: (
            _model_order(row.model),
            _OUTCOME_ORDER.get(row.outcome, len(_OUTCOME_ORDER)),
            row.bin_index,
        )
    )
    return [_calibration_bin(row) for row in rows]


def _calibration_bin(row: BacktestCalibration) -> CalibrationBin:
    """Build the DTO row of one stored calibration row."""
    return CalibrationBin(
        model=cast(ModelName, row.model),
        outcome=cast(Outcome, row.outcome),
        bin_index=row.bin_index,
        bin_lower=row.bin_lower,
        bin_upper=row.bin_upper,
        n=row.n,
        mean_predicted=row.mean_predicted,
        observed_frequency=row.observed_frequency,
    )


def _model_order(name: str) -> int:
    """Return the report position of ``name``; unknown names sort last."""
    return _MODEL_ORDER.get(name, len(BACKTEST_MODEL_NAMES))
