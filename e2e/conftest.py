"""Fixtures for the browser tests.

Every page is server rendered, so the browser never calls the API: it talks to
the dashboard only. The autouse fixtures here enforce that rule and, on demand,
save the page in both themes at both viewports.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from db import psql
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


@dataclass(frozen=True)
class PlayedForecastMatch:
    """One fixture a test flipped to played, and the score the fixture gave it."""

    match_id: int
    home_goals: int
    away_goals: int
    result: str


PLAYED_FORECAST_LIMIT = 6
"""How many scheduled fixtures the fixture flips at most."""

PLAYED_FORECAST_SCORES: tuple[tuple[int, int, str], ...] = ((2, 1, "H"), (1, 1, "D"), (0, 2, "A"))
"""The scores the flip repeats: a home win, a draw and an away win."""


def _sql_integer(value: int | None) -> str:
    """Return an integer literal, or the typed NULL the restore VALUES list needs."""
    return "NULL::integer" if value is None else str(value)


def _sql_text(value: str | None) -> str:
    """Return a quoted text literal, or the typed NULL the restore VALUES list needs."""
    if value is None:
        return "NULL::text"
    return "'" + value.replace("'", "''") + "'"


def _match_state(rows: str) -> list[tuple[int, str, int | None, int | None, str | None]]:
    """Parse the ``id|status|home_goals|away_goals|result`` lines psql returned."""
    state: list[tuple[int, str, int | None, int | None, str | None]] = []
    for line in rows.splitlines():
        if not line:
            continue
        match_id, status, home_goals, away_goals, result = line.split("|")
        state.append(
            (
                int(match_id),
                status,
                int(home_goals) if home_goals else None,
                int(away_goals) if away_goals else None,
                result or None,
            )
        )
    return state


@pytest.fixture
def played_forecast_matches() -> Iterator[list[PlayedForecastMatch]]:
    """Flip fixtures that carry a stored forecast to played, then restore them.

    The loader stores a forecast only while its match is scheduled, so the played
    state the match and performance pages need is built by updating the match row
    itself. The stored prediction rows are not touched: the result they are
    compared with changes, which is exactly the situation a played fixture with a
    genuine pre-kickoff forecast has.

    The farthest fixtures come first on purpose: the default ``/matchday`` page
    shows the nearest upcoming round and the loaded matchday tests assert on it,
    so the flip stays on the round they do not read. The original values are read
    before the update and written back in a ``finally``, so a failing test cannot
    leave the throwaway stack different from how it found it.
    """
    originals = _match_state(
        psql(
            f"""
            SELECT m.id, m.status, m.home_goals, m.away_goals, m.result
            FROM matches AS m
            WHERE m.status = 'scheduled'
              AND m.kickoff_date > (now() AT TIME ZONE 'Europe/Madrid')::date
              AND EXISTS (
                  SELECT 1 FROM predictions AS p
                  WHERE p.match_id = m.id AND p.model = 'dixon_coles'
              )
              AND EXISTS (
                  SELECT 1 FROM predictions AS p
                  WHERE p.match_id = m.id AND p.model = 'elo'
              )
            ORDER BY m.kickoff_date DESC, m.id DESC
            LIMIT {PLAYED_FORECAST_LIMIT};
            """
        )
    )
    assert len(originals) >= len(PLAYED_FORECAST_SCORES), (
        "the loaded dataset holds fewer than "
        f"{len(PLAYED_FORECAST_SCORES)} scheduled fixtures dated after today with "
        f"both a Dixon-Coles and an Elo forecast: found {len(originals)}, so the "
        "played-match tests cannot show a win, a draw and an away win"
    )
    flipped = [
        PlayedForecastMatch(match_id, *PLAYED_FORECAST_SCORES[index % len(PLAYED_FORECAST_SCORES)])
        for index, (match_id, *_rest) in enumerate(originals)
    ]
    values = ", ".join(
        f"({match.match_id}, {match.home_goals}, {match.away_goals}, {_sql_text(match.result)})"
        for match in flipped
    )
    try:
        # psql prints the returned ids and then its own ``UPDATE n`` command tag,
        # so only the numeric lines are the rows the statement changed.
        updated_ids = sorted(
            int(line)
            for line in psql(
                "UPDATE matches AS m "
                "SET status = 'played', home_goals = v.home_goals, "
                "away_goals = v.away_goals, result = v.result "
                f"FROM (VALUES {values}) AS v(id, home_goals, away_goals, result) "
                "WHERE m.id = v.id RETURNING m.id;"
            ).splitlines()
            if line.strip().isdigit()
        )
        expected_ids = sorted(match.match_id for match in flipped)
        assert updated_ids == expected_ids, (
            "the UPDATE did not return every fixture it was meant to flip: "
            f"expected {expected_ids}, got {updated_ids}"
        )
        yield flipped
    finally:
        restore_values = ", ".join(
            f"({match_id}, {_sql_text(status)}, {_sql_integer(home_goals)}, "
            f"{_sql_integer(away_goals)}, {_sql_text(result)})"
            for match_id, status, home_goals, away_goals, result in originals
        )
        psql(
            "UPDATE matches AS m "
            "SET status = v.status, home_goals = v.home_goals, "
            "away_goals = v.away_goals, result = v.result "
            f"FROM (VALUES {restore_values}) AS v(id, status, home_goals, away_goals, result) "
            "WHERE m.id = v.id;"
        )
        ids = ", ".join(str(match_id) for match_id, *_rest in originals)
        restored = _match_state(
            psql(
                "SELECT id, status, home_goals, away_goals, result FROM matches "
                f"WHERE id IN ({ids}) ORDER BY id;"
            )
        )
        expected = sorted(originals, key=lambda row: row[0])
        assert restored == expected, (
            "restoring the flipped fixtures did not put the original rows back: "
            f"expected {expected}, got {restored}"
        )
