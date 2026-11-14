"""Polite HTTP downloading shared by the ingestion parsers."""

import logging
import time
from collections.abc import Callable

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; dev-client/1.0)"

_TIMEOUT = httpx.Timeout(30.0)
_MAX_ATTEMPTS = 3
# Delay between attempt 1 and 2, then between attempt 2 and 3: it doubles each time.
_BACKOFF_SECONDS: tuple[float, ...] = (0.5, 1.0)

_logger = logging.getLogger(__name__)

# Indirection so tests can monkeypatch the sleep used by ``fetch`` and ``polite_pause``
# instead of waiting for real.
_sleep: Callable[[float], None] = time.sleep


class FetchError(RuntimeError):
    """Raised when a download cannot be completed."""


def fetch(url: str, *, client: httpx.Client | None = None) -> bytes:
    """Download ``url`` and return its body.

    Retries transport errors and 5xx responses up to ``_MAX_ATTEMPTS`` attempts
    with exponential backoff. Client errors (4xx) are not retried. A supplied
    ``client`` is used as is and never closed.
    """
    if client is not None:
        return _fetch_with(client, url)
    with httpx.Client() as owned_client:
        return _fetch_with(owned_client, url)


def _fetch_with(client: httpx.Client, url: str) -> bytes:
    last_status: int | None = None
    last_error: str | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = client.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.TransportError as exc:
            last_status = None
            last_error = f"{type(exc).__name__}: {exc}"
            # Log the URL only: never bodies or headers.
            _logger.warning("%s -> transport error %s", url, type(exc).__name__)
        else:
            if response.status_code == 200:
                if not response.content:
                    raise FetchError(f"empty body for {url}")
                _logger.info("%s -> %d", url, response.status_code)
                return response.content
            if response.status_code < 500:
                raise FetchError(f"unexpected status {response.status_code} for {url}")
            last_status = response.status_code
            last_error = None
            # Log the URL and status only: never bodies or headers.
            _logger.warning("%s -> %d", url, response.status_code)
        if attempt < _MAX_ATTEMPTS - 1:
            _sleep(_BACKOFF_SECONDS[attempt])
    detail = f"status {last_status}" if last_status is not None else f"error {last_error}"
    raise FetchError(f"failed to fetch {url}: {detail}")


def polite_pause(seconds: float = 1.0) -> None:
    """Sleep between requests so the source is not hammered."""
    if seconds < 0:
        raise ValueError(f"seconds must not be negative: {seconds}")
    _sleep(seconds)
