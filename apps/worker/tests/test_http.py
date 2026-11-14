"""Tests for the polite downloader.

``fetch`` sleeps through the module-level ``_sleep`` indirection, so every test that
can retry replaces it with a recorder and never waits for real.
"""

from collections.abc import Callable

import httpx
import pytest
from lmi_worker import http as http_module

URL = "https://example.test/SP1.csv"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def sleep_calls(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(http_module, "_sleep", fake_sleep)
    return calls


def test_fetch_follows_redirect(sleep_calls: list[float]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/SP1.csv":
            return httpx.Response(302, headers={"Location": "/moved/SP1.csv"})
        return httpx.Response(200, content=b"Date,HomeTeam\n")

    with _client(handler) as client:
        assert http_module.fetch(URL, client=client) == b"Date,HomeTeam\n"
    assert sleep_calls == []


def test_fetch_retries_server_error_then_succeeds(sleep_calls: list[float]) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=b"ok")

    with _client(handler) as client:
        assert http_module.fetch(URL, client=client) == b"ok"
    assert len(requests) == 2
    assert sleep_calls == [0.5]


def test_fetch_gives_up_after_three_server_errors(sleep_calls: list[float]) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    with _client(handler) as client, pytest.raises(http_module.FetchError, match="status 500"):
        http_module.fetch(URL, client=client)
    assert len(requests) == 3
    assert sleep_calls == [0.5, 1.0]


def test_fetch_retries_transport_errors(sleep_calls: list[float]) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, content=b"ok")

    with _client(handler) as client:
        assert http_module.fetch(URL, client=client) == b"ok"
    assert len(requests) == 2
    assert sleep_calls == [0.5]


def test_fetch_does_not_retry_client_errors(sleep_calls: list[float]) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404)

    with _client(handler) as client, pytest.raises(http_module.FetchError, match="404"):
        http_module.fetch(URL, client=client)
    assert len(requests) == 1
    assert sleep_calls == []


def test_fetch_rejects_an_empty_body(sleep_calls: list[float]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    with _client(handler) as client, pytest.raises(http_module.FetchError, match="empty body"):
        http_module.fetch(URL, client=client)


def test_fetch_sends_the_generic_user_agent() -> None:
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(200, content=b"ok")

    with _client(handler) as client:
        http_module.fetch(URL, client=client)
    assert [headers["user-agent"] for headers in seen] == [http_module.USER_AGENT]


def test_polite_pause_sleeps_the_requested_time(sleep_calls: list[float]) -> None:
    http_module.polite_pause(2.5)

    assert sleep_calls == [2.5]


def test_polite_pause_rejects_a_negative_duration(sleep_calls: list[float]) -> None:
    with pytest.raises(ValueError):
        http_module.polite_pause(-1)
    assert sleep_calls == []
