"""Season codes and calendar helpers.

A season is labelled by the two calendar years it spans, for example ``2026-27``.
A season starts on 1 July, so a date before that day belongs to the previous season.
"""

import re
from dataclasses import dataclass
from datetime import date

_SEASON_PATTERN = re.compile(r"(?P<start>[0-9]{4})-(?P<end>[0-9]{2})")
_SEASON_START_MONTH = 7
_DEFAULT_FIRST_SEASON = "2005-06"


@dataclass(frozen=True)
class SeasonCode:
    """A football season such as ``2026-27``.

    The constructor validates the year relation, so an instance always
    describes exactly two consecutive years. Use :meth:`parse` to build one
    from its canonical string.
    """

    start_year: int
    end_year: int

    def __post_init__(self) -> None:
        if self.end_year != self.start_year + 1:
            raise ValueError(
                f"invalid season: end year {self.end_year} is not one year after "
                f"start year {self.start_year}"
            )

    @classmethod
    def parse(cls, text: str) -> SeasonCode:
        """Build a season from its canonical ``YYYY-YY`` form."""
        match = _SEASON_PATTERN.fullmatch(text)
        if match is None:
            raise ValueError(
                f"invalid season code {text!r}: expected 'YYYY-YY', for example '2026-27'"
            )
        start_year = int(match.group("start"))
        end_suffix = int(match.group("end"))
        expected_suffix = (start_year + 1) % 100
        if end_suffix != expected_suffix:
            raise ValueError(
                f"invalid season code {text!r}: the second year must be exactly one year "
                f"after the first, so '{start_year}-{expected_suffix:02d}' is expected"
            )
        return cls(start_year=start_year, end_year=start_year + 1)

    @property
    def code(self) -> str:
        """Canonical form, for example ``2026-27``."""
        return f"{self.start_year}-{self.end_year % 100:02d}"

    @property
    def fd_code(self) -> str:
        """football-data.co.uk two-digit form, for example ``2627``."""
        return f"{self.start_year % 100:02d}{self.end_year % 100:02d}"

    @property
    def openfootball_path(self) -> str:
        """Season path used by the openfootball ``football.json`` layout."""
        return self.code

    def __str__(self) -> str:
        return self.code


def current_season(today: date) -> SeasonCode:
    """Return the season in progress on ``today``; a season starts on 1 July."""
    start_year = today.year if today.month >= _SEASON_START_MONTH else today.year - 1
    return SeasonCode(start_year=start_year, end_year=start_year + 1)


def last_finished(today: date) -> SeasonCode:
    """Return the most recent completed season on ``today``."""
    in_progress = current_season(today)
    return SeasonCode(start_year=in_progress.start_year - 1, end_year=in_progress.start_year)


def backfill_range(
    first: str = _DEFAULT_FIRST_SEASON,
    through: SeasonCode | None = None,
    *,
    today: date | None = None,
) -> list[SeasonCode]:
    """Return the consecutive seasons from ``first`` through ``through``, inclusive.

    When ``through`` is omitted, the last finished season on ``today`` (or on the
    current date) is used. An explicit ``through`` always wins over the calendar.
    """
    first_season = SeasonCode.parse(first)
    last_season = through if through is not None else last_finished(today or date.today())
    if first_season.start_year > last_season.start_year:
        raise ValueError(
            f"season range starts after it ends: {first_season.code} > {last_season.code}"
        )
    years = range(first_season.start_year, last_season.start_year + 1)
    return [SeasonCode(start_year=year, end_year=year + 1) for year in years]
