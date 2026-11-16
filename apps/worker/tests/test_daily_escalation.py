"""The daily cycle contains step failures, except what the Celery task must see."""

import datetime

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from lmi_worker import daily
from sqlalchemy.exc import OperationalError

TODAY = datetime.date(2026, 11, 16)


class _DriverError(Exception):
    def __init__(self, sqlstate: str | None) -> None:
        super().__init__("driver text")
        self.sqlstate = sqlstate


class _Engine:
    def dispose(self) -> None:
        pass


@pytest.fixture
def no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily, "get_settings", lambda: None)
    monkeypatch.setattr(daily, "make_engine", lambda _settings: _Engine())
    monkeypatch.setattr(daily, "session_factory", lambda _engine: _NullFactory())
    monkeypatch.setattr(daily, "_simulate", lambda *_args, **_kwargs: True)


class _NullSession:
    def __enter__(self) -> _NullSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _NullFactory:
    def __call__(self) -> _NullSession:
        return _NullSession()


def _predict_raises(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    def store(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(daily, "store_predictions", store)
    monkeypatch.setattr(daily, "_ingest", lambda *_args, **_kwargs: None)


@pytest.mark.usefixtures("no_database")
def test_a_transient_failure_is_contained_on_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _predict_raises(monkeypatch, OperationalError("SELECT 1", {}, _DriverError("08006")))
    assert daily.run_daily_cycle(today=TODAY) == 1


@pytest.mark.usefixtures("no_database")
def test_a_transient_failure_leaves_the_cycle_when_escalating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _predict_raises(monkeypatch, OperationalError("SELECT 1", {}, _DriverError("08006")))
    with pytest.raises(OperationalError):
        daily.run_daily_cycle(today=TODAY, escalate=True)


@pytest.mark.usefixtures("no_database")
def test_a_deterministic_failure_stays_contained_when_escalating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _predict_raises(monkeypatch, OperationalError("SELECT 1", {}, _DriverError("28P01")))
    assert daily.run_daily_cycle(today=TODAY, escalate=True) == 1


@pytest.mark.usefixtures("no_database")
def test_a_soft_time_limit_in_ingest_leaves_the_cycle_only_when_escalating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def plan(*_args: object, **_kwargs: object) -> None:
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(daily, "plan_jobs", plan)
    monkeypatch.setattr(daily, "store_predictions", lambda *_a, **_k: _Stored())

    with pytest.raises(SoftTimeLimitExceeded):
        daily.run_daily_cycle(today=TODAY, escalate=True)
    assert daily.run_daily_cycle(today=TODAY) == 0


@pytest.mark.usefixtures("no_database")
def test_a_network_failure_in_ingest_stays_contained_when_escalating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def plan(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("network down")

    monkeypatch.setattr(daily, "plan_jobs", plan)
    monkeypatch.setattr(daily, "store_predictions", lambda *_a, **_k: _Stored())
    assert daily.run_daily_cycle(today=TODAY, escalate=True) == 0


class _Written:
    n_inserted = 0
    n_existing = 0


class _Stored:
    run_id = "test"
    written = _Written()
