"""The Madrid calendar, the one source of dates and boundaries in this codebase.

No source provides a verified absolute kickoff instant, so a live prediction
must exist before Madrid midnight opens the day of its match. Every component
that needs that boundary, the current Madrid date or the Madrid date of an
instant asks this module instead of reading ``zoneinfo`` itself, so one change
of the rule reaches all of them.

Nothing here reads the database, accepts a timezone argument or guesses an
instant: a naive datetime names no instant on any calendar, and
:func:`madrid_date` rejects it instead of assuming UTC.
"""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

MADRID: ZoneInfo = ZoneInfo("Europe/Madrid")
"""The league calendar: Spanish wall-clock time, with its daylight saving rules."""


def madrid_now() -> datetime:
    """Return the current instant in :data:`MADRID`."""
    return datetime.now(MADRID)


def madrid_today() -> date:
    """Return the Madrid calendar date of the current instant."""
    return madrid_date(madrid_now())


def madrid_date(instant: datetime) -> date:
    """Return the Madrid calendar date of ``instant``.

    The instant is converted, not truncated: a UTC timestamp late in the evening
    names the next Madrid date, so ``.date()`` on the raw value would be wrong.

    Raises:
        ValueError: ``instant`` is naive, so it names no instant on any calendar.
    """
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError(f"a naive datetime has no Madrid calendar date: {instant!r}")
    return instant.astimezone(MADRID).date()


def kickoff_boundary(day: date) -> datetime:
    """Return the UTC instant that opens ``day`` in Madrid.

    The result is local midnight of ``day`` converted to UTC. ``zoneinfo``
    resolves the offset in force at that local time, so the boundary is
    DST-correct and differs between winter and summer: ``2026-01-15`` opens at
    ``2026-01-14T23:00Z`` (CET, UTC+1) and ``2026-07-15`` at
    ``2026-07-14T22:00Z`` (CEST, UTC+2). A day whose own midnight sits on the
    daylight-saving change keeps the offset in force at midnight:
    ``2026-03-29`` opens at ``2026-03-28T23:00Z`` and ``2026-10-25`` at
    ``2026-10-24T22:00Z``.
    """
    return datetime.combine(day, time(0, 0), tzinfo=MADRID).astimezone(UTC)
