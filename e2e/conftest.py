"""Fixtures for the browser tests.

Every page is server rendered, so the browser never calls the API: it talks to
the dashboard only. The autouse fixtures here enforce that rule and, on demand,
save the page in both themes at both viewports.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, Request

DEFAULT_BASE_URL = "http://127.0.0.1:13000"
# Ports the API is published on: the developer's `lmi` stack, the throwaway
# browser-test stack, and whatever E2E_API_PORT names.
API_PORTS = frozenset({8000, 18000})
ARTIFACTS_DIR = Path(__file__).parent / "artifacts"
VIEWPORTS = ((390, 844), (1280, 800))
COLOR_SCHEMES = ("light", "dark")


@pytest.fixture(scope="session")
def dashboard_url() -> str:
    """Base URL of the dashboard under test, without a trailing slash."""
    return os.environ.get("E2E_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def api_ports() -> frozenset[int]:
    """The host ports a browser request to the API would land on."""
    ports = set(API_PORTS)
    configured = os.environ.get("E2E_API_PORT")
    if configured:
        ports.add(int(configured))
    return frozenset(ports)


@pytest.fixture(autouse=True)
def no_browser_request_to_the_api(page: Page) -> Iterator[None]:
    """Fail the test when the browser requests the API directly."""
    ports = api_ports()
    offenders: list[str] = []

    def record(request: Request) -> None:
        url = urlsplit(request.url)
        if url.scheme in ("http", "https") and url.port in ports:
            offenders.append(request.url)

    page.on("request", record)
    yield
    assert not offenders, (
        "the browser must talk to the dashboard only, but it requested the API "
        f"directly: {', '.join(offenders)}"
    )


@pytest.fixture(autouse=True)
def screenshots(page: Page, request: pytest.FixtureRequest) -> Iterator[None]:
    """Save the page in both themes at both viewports when E2E_SCREENSHOTS=1."""
    enabled = os.environ.get("E2E_SCREENSHOTS") == "1"
    yield
    if not enabled:
        return
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    for width, height in VIEWPORTS:
        page.set_viewport_size({"width": width, "height": height})
        for scheme in COLOR_SCHEMES:
            page.emulate_media(color_scheme=scheme)
            path = ARTIFACTS_DIR / f"{request.node.name}-{width}-{scheme}.png"
            page.screenshot(path=str(path))
