"""Integration tests for the append-only backtest results store.

These tests need a live PostgreSQL and are opt-in: ``make test`` deselects them
and ``make test-integration`` starts a throwaway container. The fixture fails
rather than skips when the environment is not a disposable database, because a
missing database is a configuration error, not a missing capability.

Every test starts from an empty ``public`` schema upgraded to the head revision
and seeded, so the 26 seasons exist and the tests are independent of each other.
The store round trip is observed through the database, not through the session
that wrote it: each call gets its own session.
"""

import importlib.metadata
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from lmi_core.config import Settings
from lmi_core.db.alembic_support import upgrade_to_head
from lmi_core.db.models import BacktestCalibration, BacktestMetric, BacktestRun
from lmi_core.db.seed import seed
from lmi_core.db.session import make_engine, session_factory
from lmi_core.db.testing import require_disposable_database
from lmi_core.results.schemas import (
    CalibrationBin,
    MetricRow,
    ModelInfo,
    ModelName,
    Outcome,
    Subset,
)
from lmi_core.results.store import fail_run, finish_run, load_report, start_run
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

POSTGRES_ENVIRONMENT_VARIABLES = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)
"""The variables the fixture requires; a missing one is a hard failure."""

SEED = 42
FROM_YEAR = 2007
STEP_DAYS = 7
CODE_VERSION = "0.1.0+abc1234"
DATA_FINGERPRINT = "f" * 64
"""Run identity values the round trip reads back from the stored row."""

HOME_WIN = ModelInfo(
    name="home_win",
    kind="baseline",
    version="1.0",
    hyperparameters={"clip": 1e-15},
)
ELO = ModelInfo(
    name="elo",
    kind="model",
    version="1.0",
    hyperparameters={"k": 20, "home_advantage": 60.0},
)
MODELS = [ELO, HOME_WIN]
"""Deliberately not in report order, so the loader has to sort them."""

EXPECTED_MODELS = [HOME_WIN, ELO]
"""The report order: ``home_win`` before ``elo`` in the model constant."""


@dataclass(frozen=True)
class Database:
    """The throwaway database and the settings that reach it."""

    engine: Engine
    settings: Settings


@pytest.fixture
def database() -> Iterator[Database]:
    """Yield an engine over an empty schema at the head revision, seeded.

    Raises:
        pytest.fail: the destructive-test guard refused the environment, or one
            of the ``POSTGRES_*`` variables is missing. The message names the
            variable to set and never a value.
    """
    try:
        require_disposable_database()
    except RuntimeError as refusal:
        pytest.fail(str(refusal), pytrace=False)

    missing = [name for name in POSTGRES_ENVIRONMENT_VARIABLES if not os.environ.get(name)]
    if missing:
        pytest.fail(
            "the integration tests need a live PostgreSQL: "
            + ", ".join(missing)
            + " is not set; run `make test-integration`",
            pytrace=False,
        )

    settings = Settings()
    engine = make_engine(settings)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        upgrade_to_head(settings)
        seed(engine)
        yield Database(engine=engine, settings=settings)
    finally:
        engine.dispose()


def make_metric(
    model: ModelName,
    season_code: str | None,
    subset: Subset,
    *,
    n_matches: int,
    n_fixtures: int,
    log_loss: float,
) -> MetricRow:
    """Return one valid metric row, with coverage derived from the counts."""
    return MetricRow(
        model=model,
        season_code=season_code,
        subset=subset,
        n_matches=n_matches,
        n_fixtures=n_fixtures,
        coverage=n_matches / n_fixtures,
        log_loss=log_loss,
        brier=0.5,
        rps=0.2,
        accuracy=0.6,
    )


def make_bin(
    model: ModelName,
    outcome: Outcome,
    bin_index: int,
    *,
    mean_predicted: float,
) -> CalibrationBin:
    """Return one valid, non-empty calibration bin."""
    return CalibrationBin(
        model=model,
        outcome=outcome,
        bin_index=bin_index,
        bin_lower=bin_index / 10,
        bin_upper=(bin_index + 1) / 10,
        n=100,
        mean_predicted=mean_predicted,
        observed_frequency=mean_predicted,
    )


EXPECTED_METRICS = [
    make_metric("home_win", "2023-24", "all", n_matches=300, n_fixtures=380, log_loss=0.71),
    make_metric("home_win", "2023-24", "common", n_matches=280, n_fixtures=340, log_loss=0.72),
    make_metric("home_win", "2024-25", "all", n_matches=310, n_fixtures=380, log_loss=0.73),
    make_metric("home_win", "2024-25", "common", n_matches=290, n_fixtures=340, log_loss=0.74),
    make_metric("home_win", None, "all", n_matches=610, n_fixtures=760, log_loss=0.75),
    make_metric("home_win", None, "common", n_matches=570, n_fixtures=680, log_loss=0.76),
    make_metric("elo", "2023-24", "all", n_matches=300, n_fixtures=380, log_loss=0.61),
    make_metric("elo", "2023-24", "common", n_matches=280, n_fixtures=340, log_loss=0.62),
    make_metric("elo", "2024-25", "all", n_matches=310, n_fixtures=380, log_loss=0.63),
    make_metric("elo", "2024-25", "common", n_matches=290, n_fixtures=340, log_loss=0.64),
    make_metric("elo", None, "all", n_matches=610, n_fixtures=760, log_loss=0.65),
    make_metric("elo", None, "common", n_matches=570, n_fixtures=680, log_loss=0.66),
]
"""The D11 order: model, season start year ascending, overall last, subset."""

EXPECTED_CALIBRATION = [
    make_bin("home_win", "H", 0, mean_predicted=0.05),
    make_bin("home_win", "H", 1, mean_predicted=0.15),
    make_bin("home_win", "D", 0, mean_predicted=0.25),
    make_bin("home_win", "A", 0, mean_predicted=0.35),
    make_bin("elo", "H", 0, mean_predicted=0.06),
    make_bin("elo", "D", 0, mean_predicted=0.26),
    make_bin("elo", "A", 0, mean_predicted=0.36),
]
"""The D11 order: model, outcome H/D/A, bin index."""


def store_run(
    factory: sessionmaker[Session],
    *,
    models: list[ModelInfo],
    metrics: list[MetricRow],
    calibration: list[CalibrationBin],
    data_fingerprint: str = DATA_FINGERPRINT,
    n_matches: int = 610,
) -> uuid.UUID:
    """Start and finish one run through the store and return its id."""
    with factory() as session:
        run_id = start_run(
            session,
            seed=SEED,
            from_year=FROM_YEAR,
            step_days=STEP_DAYS,
            models=models,
            code_version=CODE_VERSION,
        )
    with factory() as session:
        finish_run(
            session,
            run_id,
            metrics=metrics,
            calibration=calibration,
            data_fingerprint=data_fingerprint,
            n_matches=n_matches,
        )
    return run_id


def test_start_run_records_the_installed_version_and_the_git_sha(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = session_factory(database.engine)
    installed = importlib.metadata.version("lmi-core")

    monkeypatch.delenv("GIT_SHA", raising=False)
    with factory() as session:
        without_sha = start_run(
            session,
            seed=SEED,
            from_year=FROM_YEAR,
            step_days=STEP_DAYS,
            models=[HOME_WIN],
        )

    monkeypatch.setenv("GIT_SHA", "abc1234")
    with factory() as session:
        with_sha = start_run(
            session,
            seed=SEED,
            from_year=FROM_YEAR,
            step_days=STEP_DAYS,
            models=[HOME_WIN],
        )

    with factory() as session:
        plain = session.get(BacktestRun, without_sha)
        tagged = session.get(BacktestRun, with_sha)
        assert plain is not None
        assert plain.code_version == installed
        assert tagged is not None
        assert tagged.code_version == f"{installed}+abc1234"


def test_load_report_returns_none_on_an_empty_database(database: Database) -> None:
    factory = session_factory(database.engine)

    with factory() as session:
        assert load_report(session) is None
        assert load_report(session, uuid.uuid4()) is None


def test_start_finish_load_round_trip(database: Database) -> None:
    factory = session_factory(database.engine)
    input_metrics = list(reversed(EXPECTED_METRICS))
    input_calibration = list(reversed(EXPECTED_CALIBRATION))
    assert input_metrics != EXPECTED_METRICS
    assert input_calibration != EXPECTED_CALIBRATION

    run_id = store_run(
        factory,
        models=MODELS,
        metrics=input_metrics,
        calibration=input_calibration,
    )

    with factory() as session:
        report = load_report(session, run_id)

    assert report is not None
    assert report.run.id == run_id
    assert report.run.seed == SEED
    assert report.run.from_year == FROM_YEAR
    assert report.run.step_days == STEP_DAYS
    assert report.run.code_version == CODE_VERSION
    assert report.run.data_fingerprint == DATA_FINGERPRINT
    assert report.run.n_matches == 610
    assert report.run.started_at is not None
    assert report.run.finished_at is not None
    assert report.run.finished_at >= report.run.started_at
    assert report.models == EXPECTED_MODELS
    assert report.metrics == EXPECTED_METRICS
    assert report.calibration == EXPECTED_CALIBRATION
    assert report.metrics[0].coverage == 300 / 380

    with factory() as session:
        stored = session.get(BacktestRun, run_id)
        assert stored is not None
        assert stored.status == "succeeded"
        assert stored.error is None


def test_load_report_returns_the_latest_succeeded_run(database: Database) -> None:
    factory = session_factory(database.engine)
    metric = make_metric("home_win", None, "all", n_matches=300, n_fixtures=380, log_loss=0.7)
    first_id = store_run(
        factory,
        models=[HOME_WIN],
        metrics=[metric],
        calibration=[],
        data_fingerprint="1" * 64,
        n_matches=300,
    )
    second_id = store_run(
        factory,
        models=[HOME_WIN],
        metrics=[metric],
        calibration=[],
        data_fingerprint="2" * 64,
        n_matches=300,
    )

    with factory() as session:
        latest_started = max(
            run.started_at
            for run in session.scalars(select(BacktestRun))
            if run.started_at is not None
        )
        report = load_report(session)

    assert report is not None
    assert report.run.id in {first_id, second_id}
    assert report.run.started_at == latest_started


def test_fail_run_records_the_error_and_is_never_reported(database: Database) -> None:
    factory = session_factory(database.engine)
    with factory() as session:
        running_id = start_run(
            session,
            seed=SEED,
            from_year=FROM_YEAR,
            step_days=STEP_DAYS,
            models=MODELS,
            code_version=CODE_VERSION,
        )
        failed_id = start_run(
            session,
            seed=SEED + 1,
            from_year=FROM_YEAR,
            step_days=STEP_DAYS,
            models=MODELS,
            code_version=CODE_VERSION,
        )

    with factory() as session:
        fail_run(session, failed_id, error="dixon_coles fit did not converge")

    with factory() as session:
        failed = session.get(BacktestRun, failed_id)
        running = session.get(BacktestRun, running_id)
        assert failed is not None
        assert failed.status == "failed"
        assert failed.error == "dixon_coles fit did not converge"
        assert failed.finished_at is not None
        assert failed.data_fingerprint is None
        assert failed.n_matches is None
        assert running is not None
        assert running.status == "running"
        assert running.finished_at is None

    with factory() as session:
        assert load_report(session) is None
        assert load_report(session, failed_id) is None
        assert load_report(session, running_id) is None


def test_finish_run_rejects_an_unknown_season_and_stores_nothing(database: Database) -> None:
    factory = session_factory(database.engine)
    with factory() as session:
        run_id = start_run(
            session,
            seed=SEED,
            from_year=FROM_YEAR,
            step_days=STEP_DAYS,
            models=[HOME_WIN],
            code_version=CODE_VERSION,
        )
    metric = make_metric("home_win", "1999-00", "all", n_matches=300, n_fixtures=380, log_loss=0.7)

    with factory() as session, pytest.raises(ValueError, match="1999-00"):
        finish_run(
            session,
            run_id,
            metrics=[metric],
            calibration=[],
            data_fingerprint=DATA_FINGERPRINT,
            n_matches=300,
        )

    with factory() as session:
        stored = session.get(BacktestRun, run_id)
        assert stored is not None
        assert stored.status == "running"
        assert stored.finished_at is None
        assert stored.data_fingerprint is None
        assert stored.n_matches is None
        assert session.scalar(select(func.count()).select_from(BacktestMetric)) == 0


def test_finish_and_fail_reject_unknown_and_finished_runs(database: Database) -> None:
    factory = session_factory(database.engine)
    metric = make_metric("home_win", None, "all", n_matches=300, n_fixtures=380, log_loss=0.7)
    with factory() as session:
        succeeded_id = start_run(
            session,
            seed=SEED,
            from_year=FROM_YEAR,
            step_days=STEP_DAYS,
            models=[HOME_WIN],
            code_version=CODE_VERSION,
        )
    with factory() as session:
        finish_run(
            session,
            succeeded_id,
            metrics=[metric],
            calibration=[],
            data_fingerprint="a" * 64,
            n_matches=300,
        )
    with factory() as session:
        succeeded = session.get(BacktestRun, succeeded_id)
        assert succeeded is not None
        assert succeeded.status == "succeeded"
        succeeded_finished_at = succeeded.finished_at

    with factory() as session, pytest.raises(ValueError, match="does not exist"):
        finish_run(
            session,
            uuid.uuid4(),
            metrics=[metric],
            calibration=[],
            data_fingerprint="b" * 64,
            n_matches=300,
        )
    with factory() as session, pytest.raises(ValueError, match="does not exist"):
        fail_run(session, uuid.uuid4(), error="unknown run")

    with factory() as session, pytest.raises(ValueError, match="not 'running'"):
        finish_run(
            session,
            succeeded_id,
            metrics=[metric],
            calibration=[],
            data_fingerprint="c" * 64,
            n_matches=300,
        )
    with factory() as session, pytest.raises(ValueError, match="not 'running'"):
        fail_run(session, succeeded_id, error="too late")

    with factory() as session:
        succeeded = session.get(BacktestRun, succeeded_id)
        assert succeeded is not None
        assert succeeded.status == "succeeded"
        assert succeeded.finished_at == succeeded_finished_at
        assert succeeded.data_fingerprint == "a" * 64
        assert succeeded.n_matches == 300
        assert succeeded.error is None
        assert session.scalar(select(func.count()).select_from(BacktestMetric)) == 1

    with factory() as session:
        failed_id = start_run(
            session,
            seed=SEED + 2,
            from_year=FROM_YEAR,
            step_days=STEP_DAYS,
            models=[HOME_WIN],
            code_version=CODE_VERSION,
        )
    with factory() as session:
        fail_run(session, failed_id, error="the fit diverged")

    with factory() as session, pytest.raises(ValueError, match="not 'running'"):
        finish_run(
            session,
            failed_id,
            metrics=[metric],
            calibration=[],
            data_fingerprint="d" * 64,
            n_matches=300,
        )
    with factory() as session, pytest.raises(ValueError, match="not 'running'"):
        fail_run(session, failed_id, error="a second failure")

    with factory() as session:
        failed = session.get(BacktestRun, failed_id)
        assert failed is not None
        assert failed.status == "failed"
        assert failed.error == "the fit diverged"
        assert session.scalar(select(func.count()).select_from(BacktestMetric)) == 1


def start_running(factory: sessionmaker[Session]) -> uuid.UUID:
    """Insert one ``running`` run through the store and return its id."""
    with factory() as session:
        return start_run(
            session,
            seed=SEED,
            from_year=FROM_YEAR,
            step_days=STEP_DAYS,
            models=[HOME_WIN],
            code_version=CODE_VERSION,
        )


def test_finish_run_rejects_a_negative_n_matches_and_stores_nothing(
    database: Database,
) -> None:
    factory = session_factory(database.engine)
    run_id = start_running(factory)
    metric = make_metric("home_win", None, "all", n_matches=300, n_fixtures=380, log_loss=0.7)

    with factory() as session, pytest.raises(ValueError, match="non-negative"):
        finish_run(
            session,
            run_id,
            metrics=[metric],
            calibration=[make_bin("home_win", "H", 0, mean_predicted=0.05)],
            data_fingerprint=DATA_FINGERPRINT,
            n_matches=-1,
        )

    with factory() as session:
        stored = session.get(BacktestRun, run_id)
        assert stored is not None
        assert stored.status == "running"
        assert stored.finished_at is None
        assert stored.data_fingerprint is None
        assert stored.n_matches is None
        assert stored.error is None
        assert session.scalar(select(func.count()).select_from(BacktestMetric)) == 0
        assert session.scalar(select(func.count()).select_from(BacktestCalibration)) == 0


def test_a_second_terminal_transition_cannot_overwrite_the_first(
    database: Database,
) -> None:
    factory = session_factory(database.engine)
    run_id = start_running(factory)
    metric = make_metric("home_win", None, "all", n_matches=300, n_fixtures=380, log_loss=0.7)

    with factory() as first, factory() as second:
        loaded = first.get(BacktestRun, run_id)
        assert loaded is not None
        assert loaded.status == "running"

        fail_run(second, run_id, error="the worker died mid-run")

        with pytest.raises(ValueError, match="not 'running'"):
            finish_run(
                first,
                run_id,
                metrics=[metric],
                calibration=[],
                data_fingerprint=DATA_FINGERPRINT,
                n_matches=300,
            )

    with factory() as session:
        stored = session.get(BacktestRun, run_id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.error == "the worker died mid-run"
        assert stored.finished_at is not None
        assert stored.data_fingerprint is None
        assert stored.n_matches is None
        assert session.scalar(select(func.count()).select_from(BacktestMetric)) == 0
        assert load_report(session, run_id) is None


def test_a_duplicate_metric_row_rolls_the_whole_finish_back(database: Database) -> None:
    factory = session_factory(database.engine)
    run_id = start_running(factory)
    overall = make_metric("home_win", None, "all", n_matches=300, n_fixtures=380, log_loss=0.7)
    duplicate = make_metric("home_win", None, "all", n_matches=300, n_fixtures=380, log_loss=0.8)

    with factory() as session, pytest.raises(IntegrityError, match="ux_backtest_metrics_overall"):
        finish_run(
            session,
            run_id,
            metrics=[overall, duplicate],
            calibration=[make_bin("home_win", "H", 0, mean_predicted=0.05)],
            data_fingerprint=DATA_FINGERPRINT,
            n_matches=300,
        )

    with factory() as session:
        stored = session.get(BacktestRun, run_id)
        assert stored is not None
        assert stored.status == "running"
        assert stored.finished_at is None
        assert stored.data_fingerprint is None
        assert stored.n_matches is None
        assert session.scalar(select(func.count()).select_from(BacktestMetric)) == 0
        assert session.scalar(select(func.count()).select_from(BacktestCalibration)) == 0


def test_a_check_violation_leaves_the_session_usable(database: Database) -> None:
    factory = session_factory(database.engine)

    with factory() as session:
        with pytest.raises(IntegrityError, match="step_days_at_least_one"):
            start_run(
                session,
                seed=SEED,
                from_year=FROM_YEAR,
                step_days=0,
                models=[HOME_WIN],
                code_version=CODE_VERSION,
            )

        # No caller-side rollback: the store rolled its own transaction back.
        assert session.scalar(select(func.count()).select_from(BacktestRun)) == 0
        run_id = start_run(
            session,
            seed=SEED,
            from_year=FROM_YEAR,
            step_days=STEP_DAYS,
            models=[HOME_WIN],
            code_version=CODE_VERSION,
        )

    with factory() as session:
        stored = session.get(BacktestRun, run_id)
        assert stored is not None
        assert stored.step_days == STEP_DAYS
        assert stored.status == "running"


def test_a_rejected_transition_releases_the_row_lock(database: Database) -> None:
    """A caller that catches the rejection must not keep the run row locked.

    ``finish_run`` and ``fail_run`` take ``SELECT ... FOR UPDATE`` on the run row
    before they can reject it, so both rejection paths must roll their
    transaction back. The observer sets a short ``statement_timeout``: a lock
    the rejection leaked fails here instead of hanging the suite.
    """
    factory = session_factory(database.engine)
    failed_id = start_running(factory)
    unknown_season_id = start_running(factory)
    unknown_season = make_metric(
        "home_win", "1999-00", "all", n_matches=300, n_fixtures=380, log_loss=0.7
    )
    observe = text("UPDATE backtest_runs SET error = :error WHERE id = :run_id")

    with factory() as rejecting, factory() as observer:
        observer.execute(text("SET statement_timeout = '2s'"))

        fail_run(rejecting, failed_id, error="the worker died mid-run")
        with pytest.raises(ValueError, match="not 'running'"):
            fail_run(rejecting, failed_id, error="a second failure")
        observer.execute(
            observe, {"error": "observed after the terminal rejection", "run_id": failed_id}
        )
        assert (
            observer.scalar(
                text("SELECT error FROM backtest_runs WHERE id = :run_id"), {"run_id": failed_id}
            )
            == "observed after the terminal rejection"
        )

        with pytest.raises(ValueError, match="1999-00"):
            finish_run(
                rejecting,
                unknown_season_id,
                metrics=[unknown_season],
                calibration=[],
                data_fingerprint=DATA_FINGERPRINT,
                n_matches=300,
            )
        observer.execute(
            observe,
            {"error": "observed after the season rejection", "run_id": unknown_season_id},
        )
        assert (
            observer.scalar(
                text("SELECT error FROM backtest_runs WHERE id = :run_id"),
                {"run_id": unknown_season_id},
            )
            == "observed after the season rejection"
        )
