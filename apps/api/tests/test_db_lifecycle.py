"""Tests for the request session and the process-wide engine lifecycle.

Nothing here opens a session or an engine: the database factories are replaced
with fakes that record what the lifecycle asks of them, so this module carries
no ``integration`` marker and runs in ``make test`` on a machine with no
PostgreSQL, exactly like ``test_errors.py``.

The subject is two behaviors that only show at a lifecycle boundary. The
request-scoped session is closed when a request ends, whether the route returned
or raised, and the application lifespan disposes the engine a request built and
empties both process-wide caches on shutdown.

``get_engine`` and ``get_session_factory`` are ``functools.cache``-decorated
module globals shared by the whole process, so :func:`empty_engine_caches` runs
around every test. A fake engine left in either cache would be visible to the
next test, to ``test_health.py`` or to ``conftest.py``.
"""

import asyncio
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lmi_api import db
from lmi_api.db import SessionDep, get_engine, get_session_factory
from lmi_api.main import create_app

PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type the unhandled-error handler answers with."""


class RecordingSession:
    """A session stand-in that only records that it was closed."""

    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class RecordingSessionFactory:
    """A session factory that records every session it hands out."""

    def __init__(self) -> None:
        self.sessions: list[RecordingSession] = []

    def __call__(self) -> RecordingSession:
        session = RecordingSession()
        self.sessions.append(session)
        return session


class RecordingEngine:
    """An engine stand-in that only records that it was disposed."""

    def __init__(self) -> None:
        self.dispose_calls = 0

    def dispose(self) -> None:
        self.dispose_calls += 1


@pytest.fixture(autouse=True)
def empty_engine_caches() -> Iterator[None]:
    """Empty both process-wide caches before and after every test.

    The fakes below fill caches the whole process shares. Clearing on both sides
    keeps a later test, and the other API test modules, from observing a fake
    engine or a fake session factory.
    """
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    try:
        yield
    finally:
        get_engine.cache_clear()
        get_session_factory.cache_clear()


def _application_with_session_routes() -> FastAPI:
    """Return a fresh application with one returning and one raising route.

    Both routes depend on the request session, so the dependency is built and
    torn down around each request. They are added to a freshly built
    application, never to the module-level one.
    """
    application = create_app()

    @application.get("/test/session-returns")
    def read_with_session(_session: SessionDep) -> dict[str, bool]:
        return {"ok": True}

    @application.get("/test/session-raises")
    def raise_with_session(_session: SessionDep) -> None:
        raise RuntimeError("the route failed after the dependency was built")

    return application


def test_get_session_closes_the_session_when_the_route_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = RecordingSessionFactory()
    monkeypatch.setattr(db, "get_session_factory", lambda: factory)
    application = _application_with_session_routes()

    with TestClient(application) as client:
        response = client.get("/test/session-returns")

    assert response.status_code == 200
    assert len(factory.sessions) == 1
    assert factory.sessions[0].close_calls == 1


def test_get_session_closes_the_session_when_the_route_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = RecordingSessionFactory()
    monkeypatch.setattr(db, "get_session_factory", lambda: factory)
    application = _application_with_session_routes()

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/test/session-raises")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json()["status"] == 500
    assert len(factory.sessions) == 1
    assert factory.sessions[0].close_calls == 1


def test_lifespan_disposes_the_engine_and_empties_both_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = RecordingEngine()
    factory = RecordingSessionFactory()
    monkeypatch.setattr(db, "make_engine", lambda _settings: engine)
    monkeypatch.setattr(db, "get_settings", lambda: object())
    monkeypatch.setattr(db, "session_factory", lambda _engine: factory)
    application = create_app()

    async def run_application_lifecycle() -> None:
        async with db.lifespan(application):
            created_engine: object = get_engine()
            created_factory: object = get_session_factory()
            assert created_engine is engine
            assert created_factory is factory

    asyncio.run(run_application_lifecycle())

    assert engine.dispose_calls == 1
    assert get_engine.cache_info().currsize == 0
    assert get_session_factory.cache_info().currsize == 0


def test_lifespan_creates_and_disposes_nothing_without_an_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = RecordingEngine()
    make_engine_calls: list[object] = []

    def recording_make_engine(settings: object) -> RecordingEngine:
        make_engine_calls.append(settings)
        return engine

    monkeypatch.setattr(db, "make_engine", recording_make_engine)
    monkeypatch.setattr(db, "get_settings", lambda: object())
    monkeypatch.setattr(db, "session_factory", lambda _engine: RecordingSessionFactory())
    application = create_app()

    async def run_application_lifecycle() -> None:
        async with db.lifespan(application):
            assert get_engine.cache_info().currsize == 0
            assert get_session_factory.cache_info().currsize == 0

    asyncio.run(run_application_lifecycle())

    assert make_engine_calls == []
    assert engine.dispose_calls == 0
    assert get_engine.cache_info().currsize == 0
    assert get_session_factory.cache_info().currsize == 0
