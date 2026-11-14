"""Tests for season codes and calendar helpers."""

from datetime import date

import pytest
from lmi_worker.seasons import SeasonCode, backfill_range, current_season, last_finished


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 6, 30), "2025-26"),
        (date(2026, 7, 1), "2026-27"),
        (date(2026, 8, 15), "2026-27"),
        (date(2026, 1, 10), "2025-26"),
        (date(2026, 11, 13), "2026-27"),
    ],
)
def test_current_season_starts_on_first_of_july(today: date, expected: str) -> None:
    assert current_season(today).code == expected


def test_last_finished_is_the_season_before_the_current_one() -> None:
    assert last_finished(date(2026, 11, 13)).code == "2025-26"


def test_parse_exposes_canonical_fields() -> None:
    season = SeasonCode.parse("2026-27")

    assert season.code == "2026-27"
    assert str(season) == "2026-27"
    assert season.start_year == 2026
    assert season.end_year == 2027


@pytest.mark.parametrize(
    ("code", "fd_code"),
    [("2026-27", "2627"), ("2005-06", "0506")],
)
def test_fd_code_uses_two_digit_years(code: str, fd_code: str) -> None:
    assert SeasonCode.parse(code).fd_code == fd_code


def test_openfootball_path_round_trips() -> None:
    season = SeasonCode.parse("2026-27")

    assert season.openfootball_path == "2026-27"
    assert SeasonCode.parse(season.openfootball_path) == season


@pytest.mark.parametrize(
    "text",
    [
        "2026-28",
        "26-27",
        "2026/27",
        "",
        "2026-27\n",
        " 2026-27",
        "2026-27 ",
        "\uff12\uff10\uff12\uff16-27",
    ],
)
def test_parse_rejects_invalid_codes(text: str) -> None:
    with pytest.raises(ValueError):
        SeasonCode.parse(text)


def test_backfill_range_defaults_to_the_last_finished_season() -> None:
    seasons = backfill_range(today=date(2026, 11, 13))

    assert seasons[0].code == "2005-06"
    assert seasons[-1].code == "2025-26"
    assert len(seasons) == 21


def test_backfill_range_honours_an_explicit_through() -> None:
    seasons = backfill_range(through=SeasonCode.parse("2010-11"), today=date(2026, 11, 13))

    assert seasons[0].code == "2005-06"
    assert seasons[-1].code == "2010-11"
    assert len(seasons) == 6


def test_backfill_range_rejects_a_reversed_range() -> None:
    with pytest.raises(ValueError):
        backfill_range("2026-27", through=SeasonCode.parse("2025-26"))
