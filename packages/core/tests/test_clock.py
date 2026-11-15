"""Tests for :mod:`lmi_core.clock`."""

from datetime import UTC, date, datetime, timedelta

import pytest
from lmi_core import clock
from lmi_core.clock import MADRID, kickoff_boundary, madrid_date, madrid_now, madrid_today


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        # Winter: CET, UTC+1, so the boundary is 23:00Z of the previous day.
        (date(2026, 1, 15), datetime(2026, 1, 14, 23, 0, tzinfo=UTC)),
        # Summer: CEST, UTC+2, so the boundary is 22:00Z of the previous day.
        (date(2026, 7, 15), datetime(2026, 7, 14, 22, 0, tzinfo=UTC)),
        # The two days whose own midnight sits on the daylight-saving change.
        (date(2026, 3, 29), datetime(2026, 3, 28, 23, 0, tzinfo=UTC)),
        (date(2026, 10, 25), datetime(2026, 10, 24, 22, 0, tzinfo=UTC)),
    ],
)
def test_kickoff_boundary_opens_the_match_date_in_utc(day: date, expected: datetime) -> None:
    boundary = kickoff_boundary(day)

    assert boundary == expected
    assert boundary.tzinfo is not None
    assert boundary.utcoffset() == timedelta(0)
    assert madrid_date(boundary) == day


def test_the_boundary_keeps_the_madrid_offset_of_its_own_midnight() -> None:
    winter = kickoff_boundary(date(2026, 1, 15))
    summer = kickoff_boundary(date(2026, 7, 15))

    assert winter.astimezone(MADRID).utcoffset() == timedelta(hours=1)
    assert summer.astimezone(MADRID).utcoffset() == timedelta(hours=2)
    assert winter.astimezone(MADRID).hour == summer.astimezone(MADRID).hour == 0


def test_madrid_date_converts_the_instant_instead_of_truncating_it() -> None:
    late_evening = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)

    assert late_evening.date() == date(2026, 1, 15)
    assert madrid_date(late_evening) == date(2026, 1, 16)


def test_madrid_date_rejects_a_naive_datetime() -> None:
    with pytest.raises(ValueError, match="naive"):
        madrid_date(datetime(2026, 1, 15, 12, 0))


def test_madrid_now_is_aware_and_in_madrid() -> None:
    now = madrid_now()

    assert now.tzinfo is not None
    assert now.utcoffset() is not None
    assert now.astimezone(MADRID) == now


def test_madrid_today_is_the_madrid_date_of_the_current_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The clock is frozen, so the assertion cannot flake when Madrid crosses
    # midnight between the two calls.
    instant = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
    monkeypatch.setattr(clock, "madrid_now", lambda: instant)

    assert madrid_today() == madrid_date(clock.madrid_now()) == date(2026, 1, 16)
