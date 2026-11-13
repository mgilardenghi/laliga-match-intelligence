"""Parse football-data.co.uk season files into a validated snapshot.

The parser is pure: it decodes the bytes of one league season file and returns
frozen models. It performs no I/O, touches no database and never rewrites team
names beyond Unicode NFC and whitespace collapsing, because the loader owns the
club aliases.

Columns are always read by header name, never by position, since the header width
drifts between 61 and 131 columns across seasons. Unknown extra columns are
ignored and absent optional columns yield ``None`` instead of an error.

A single unusable row never raises: it becomes an :class:`FdRejection` and the
rest of the file is still returned. Only a file that cannot be used as a whole
raises :class:`FdSnapshotError`.
"""

import csv
import io
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date as date_type
from datetime import datetime
from datetime import time as time_type
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

Bookmaker = Literal["bet365", "pinnacle", "market_avg", "market_max"]
"""A bookmaker family collapsed onto the canonical keys of the odds map."""

MatchResult = Literal["H", "D", "A"]
"""Full-time result from the home team's point of view."""

_DIVISION = "SP1"
_CORE_COLUMNS: tuple[str, ...] = ("Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR")
_DATE_FORMATS: tuple[str, ...] = ("%d/%m/%Y", "%d/%m/%y")
_TIME_PATTERN = re.compile(r"\d{2}:\d{2}")
_RESULTS: dict[str, MatchResult] = {"H": "H", "D": "D", "A": "A"}
_ZERO = Decimal(0)
_MINIMUM_ODDS = Decimal(1)

# Ordered canonical odds map: (bookmaker, is_closing, (home, draw, away) columns).
# Group order is the emission order, so a fixed vintage always yields a fixed order.
_ODDS_GROUPS: tuple[tuple[Bookmaker, bool, tuple[str, str, str]], ...] = (
    ("bet365", False, ("B365H", "B365D", "B365A")),
    ("bet365", True, ("B365CH", "B365CD", "B365CA")),
    ("pinnacle", False, ("PSH", "PSD", "PSA")),
    ("pinnacle", False, ("PPH", "PPD", "PPA")),
    ("pinnacle", True, ("PSCH", "PSCD", "PSCA")),
    ("pinnacle", True, ("PPCH", "PPCD", "PPCA")),
    ("market_avg", False, ("BbAvH", "BbAvD", "BbAvA")),
    ("market_avg", False, ("AvgH", "AvgD", "AvgA")),
    ("market_avg", True, ("AvgCH", "AvgCD", "AvgCA")),
    ("market_max", False, ("BbMxH", "BbMxD", "BbMxA")),
    ("market_max", False, ("MaxH", "MaxD", "MaxA")),
    ("market_max", True, ("MaxCH", "MaxCD", "MaxCA")),
)


class FdSnapshotError(ValueError):
    """Raised when a whole file is unusable, for example because it is empty."""


class _RowError(Exception):
    """Internal: one row cannot be turned into a match; it becomes a rejection."""


class _FrozenModel(BaseModel):
    """Shared configuration for the immutable snapshot models."""

    model_config = ConfigDict(frozen=True)


class FdRejection(_FrozenModel):
    """One row that was skipped, with the reason and the raw cells."""

    row_number: int
    reason: str
    raw: dict[str, str]


class FdStats(_FrozenModel):
    """Per-match team statistics; ``None`` means the source had no value."""

    home_shots: int | None = None
    away_shots: int | None = None
    home_shots_on_target: int | None = None
    away_shots_on_target: int | None = None
    home_fouls: int | None = None
    away_fouls: int | None = None
    home_corners: int | None = None
    away_corners: int | None = None
    home_yellow: int | None = None
    away_yellow: int | None = None
    home_red: int | None = None
    away_red: int | None = None


class OddsQuote(_FrozenModel):
    """One 1X2 quote from one bookmaker family for one match."""

    bookmaker: Bookmaker
    is_closing: bool
    home: Decimal
    draw: Decimal
    away: Decimal


class FdMatch(_FrozenModel):
    """One finished match with its statistics and the odds that were present."""

    date: date_type
    time: time_type | None
    home: str
    away: str
    fthg: int
    ftag: int
    ftr: MatchResult
    hthg: int | None
    htag: int | None
    stats: FdStats
    home_xg: Decimal | None
    away_xg: Decimal | None
    odds: list[OddsQuote]


class FdSnapshot(_FrozenModel):
    """Every accepted match of one season file plus the rows that were skipped."""

    matches: list[FdMatch]
    rejections: list[FdRejection]


def parse_csv(content: bytes) -> FdSnapshot:
    """Parse one football-data.co.uk season file.

    Rows that cannot be used are collected in ``rejections`` instead of raising.
    Raises :class:`FdSnapshotError` when the file as a whole is unusable: not
    UTF-8, empty, header only, a missing core column, a row outside ``SP1``, or a
    fixture that appears twice.
    """
    text = _decode(content)
    if not text.strip():
        raise FdSnapshotError("the file is empty")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    column_names = reader.fieldnames or []
    if not column_names:
        raise FdSnapshotError("the file has no header row")
    header = frozenset(column_names)
    absent = [column for column in _CORE_COLUMNS if column not in header]
    if absent:
        raise FdSnapshotError(f"missing core column(s) in the header: {', '.join(absent)}")

    matches: list[FdMatch] = []
    rejections: list[FdRejection] = []
    data_rows = 0
    for row_number, raw_row in enumerate(reader, start=2):
        # A short row gives ``None`` values, extra fields land under key ``None``.
        cells, short_columns, extra_values = _split_row(cast("dict[str | None, object]", raw_row))
        if not any(cells.values()) and not extra_values:
            # A trailing blank row: every value is empty or missing.
            continue
        data_rows += 1
        raw = {column: cells.get(column, "") for column in column_names}
        if extra_values:
            rejections.append(
                FdRejection(
                    row_number=row_number,
                    reason=f"{len(extra_values)} value(s) beyond the header columns",
                    raw=raw,
                )
            )
            continue
        if short_columns:
            rejections.append(
                FdRejection(
                    row_number=row_number,
                    reason=f"row ends early: no value for {', '.join(short_columns)}",
                    raw=raw,
                )
            )
            continue
        division = _cell(cells, "Div")
        if division != _DIVISION:
            raise FdSnapshotError(
                f"row {row_number}: division {division!r} is not {_DIVISION}; "
                f"only La Liga season files are accepted"
            )
        try:
            match = _build_match(cells, header)
        except _RowError as error:
            rejections.append(FdRejection(row_number=row_number, reason=str(error), raw=raw))
        else:
            matches.append(match)

    if data_rows == 0:
        raise FdSnapshotError("the file has a header but no data rows")
    _reject_duplicate_fixtures(matches)
    return FdSnapshot(matches=matches, rejections=rejections)


def _decode(content: bytes) -> str:
    """Decode the body as UTF-8, dropping a byte order mark if there is one."""
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise FdSnapshotError(
            f"the file is not valid UTF-8: {error.reason} at byte {error.start}"
        ) from error


def _split_row(
    raw_row: Mapping[str | None, object],
) -> tuple[dict[str, str], list[str], list[object]]:
    """Split a ``DictReader`` row into cells, short-row columns and extra values."""
    cells: dict[str, str] = {}
    short_columns: list[str] = []
    extra_values: list[object] = []
    for key, value in raw_row.items():
        if key is None:
            # ``DictReader`` collects every value beyond the header width here.
            extra_values.extend(value if isinstance(value, list) else [value])
            continue
        if value is None:
            # The row ended before this header column.
            short_columns.append(key)
        else:
            cells[key] = value if isinstance(value, str) else ""
    return cells, short_columns, extra_values


def _reject_duplicate_fixtures(matches: Sequence[FdMatch]) -> None:
    """Raise when the same home and away pair was accepted twice."""
    seen: set[tuple[str, str]] = set()
    for match in matches:
        pair = (match.home, match.away)
        if pair in seen:
            raise FdSnapshotError(
                f"duplicate fixture: {pair[0]!r} vs {pair[1]!r} appears more than once"
            )
        seen.add(pair)


def _build_match(cells: Mapping[str, str], header: frozenset[str]) -> FdMatch:
    """Turn one row into a match, or raise :class:`_RowError` naming the column."""
    home = _team_name(cells, "HomeTeam")
    away = _team_name(cells, "AwayTeam")
    fthg = _required_int(cells, "FTHG")
    ftag = _required_int(cells, "FTAG")
    return FdMatch(
        date=_date(cells, "Date"),
        time=_time(cells, "Time"),
        home=home,
        away=away,
        fthg=fthg,
        ftag=ftag,
        ftr=_result(cells, fthg=fthg, ftag=ftag),
        hthg=_int_value(cells, "HTHG"),
        htag=_int_value(cells, "HTAG"),
        stats=FdStats(
            home_shots=_int_value(cells, "HS"),
            away_shots=_int_value(cells, "AS"),
            home_shots_on_target=_int_value(cells, "HST"),
            away_shots_on_target=_int_value(cells, "AST"),
            home_fouls=_int_value(cells, "HF"),
            away_fouls=_int_value(cells, "AF"),
            home_corners=_int_value(cells, "HC"),
            away_corners=_int_value(cells, "AC"),
            home_yellow=_int_value(cells, "HY"),
            away_yellow=_int_value(cells, "AY"),
            home_red=_int_value(cells, "HR"),
            away_red=_int_value(cells, "AR"),
        ),
        home_xg=_expected_goals(cells, "HxG"),
        away_xg=_expected_goals(cells, "AxG"),
        odds=_odds_quotes(cells, header),
    )


def _cell(cells: Mapping[str, str], column: str) -> str:
    """Return a stripped cell; an absent column reads like an empty one."""
    return cells.get(column, "").strip()


def _team_name(cells: Mapping[str, str], column: str) -> str:
    """Normalize a team name to NFC with single spaces, keeping it unaliased."""
    normalized = unicodedata.normalize("NFC", _cell(cells, column))
    name = " ".join(normalized.split())
    if not name:
        raise _RowError(f"{column}: name is empty")
    return name


def _int_value(cells: Mapping[str, str], column: str) -> int | None:
    """Read an optional non-negative integer; ``None`` when the cell is empty."""
    text = _cell(cells, column)
    if not text:
        return None
    try:
        number = int(text)
    except ValueError:
        raise _RowError(f"{column}: {text!r} is not an integer") from None
    if number < 0:
        raise _RowError(f"{column}: {number} must not be negative")
    return number


def _required_int(cells: Mapping[str, str], column: str) -> int:
    """Read a required non-negative integer."""
    number = _int_value(cells, column)
    if number is None:
        raise _RowError(f"{column}: value is missing")
    return number


def _decimal_value(cells: Mapping[str, str], column: str) -> Decimal | None:
    """Read an optional finite decimal; ``None`` when the cell is empty."""
    text = _cell(cells, column)
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise _RowError(f"{column}: {text!r} is not a decimal number") from None
    if not value.is_finite():
        raise _RowError(f"{column}: {text!r} is not a finite number")
    return value


def _expected_goals(cells: Mapping[str, str], column: str) -> Decimal | None:
    """Read an optional expected-goals value, which must not be negative."""
    value = _decimal_value(cells, column)
    if value is not None and value < _ZERO:
        raise _RowError(f"{column}: {value} must not be negative")
    return value


def _odds_decimal(cells: Mapping[str, str], column: str) -> Decimal:
    """Read one odds value, which must be a decimal greater than one."""
    value = _decimal_value(cells, column)
    if value is None or value <= _MINIMUM_ODDS:
        raise _RowError(f"{column}: {_cell(cells, column)!r} must be greater than 1")
    return value


def _date(cells: Mapping[str, str], column: str) -> date_type:
    """Read a ``DD/MM/YY`` or ``DD/MM/YYYY`` date."""
    text = _cell(cells, column)
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    raise _RowError(f"{column}: {text!r} is not a DD/MM/YY or DD/MM/YYYY date")


def _time(cells: Mapping[str, str], column: str) -> time_type | None:
    """Read an optional ``HH:MM`` kickoff time as a naive time."""
    text = _cell(cells, column)
    if not text:
        return None
    if _TIME_PATTERN.fullmatch(text) is None:
        raise _RowError(f"{column}: {text!r} is not an HH:MM time")
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError:
        raise _RowError(f"{column}: {text!r} is not an HH:MM time") from None


def _result(cells: Mapping[str, str], *, fthg: int, ftag: int) -> MatchResult:
    """Read the full-time result and check it against the score."""
    text = _cell(cells, "FTR")
    result = _RESULTS.get(text)
    if result is None:
        raise _RowError(f"FTR: {text!r} is not one of H, D, A")
    expected: MatchResult = "H" if fthg > ftag else "D" if fthg == ftag else "A"
    if result != expected:
        raise _RowError(
            f"FTR: {text!r} disagrees with the score {fthg}-{ftag}, which is {expected}"
        )
    return result


def _odds_quotes(cells: Mapping[str, str], header: frozenset[str]) -> list[OddsQuote]:
    """Build the canonical quotes of a row, in table order.

    A group is emitted only when its three columns exist and all three values are
    filled; a partially filled group is silently omitted. Two complete groups for
    the same slot make the row ambiguous and reject it.
    """
    quotes: list[OddsQuote] = []
    used: set[tuple[Bookmaker, bool]] = set()
    for bookmaker, is_closing, columns in _ODDS_GROUPS:
        if not all(column in header for column in columns):
            continue
        if not all(_cell(cells, column) for column in columns):
            continue
        slot = (bookmaker, is_closing)
        if slot in used:
            kind = "closing" if is_closing else "opening"
            raise _RowError(
                f"{', '.join(columns)}: a second {bookmaker} {kind} quote makes the row ambiguous"
            )
        used.add(slot)
        quotes.append(
            OddsQuote(
                bookmaker=bookmaker,
                is_closing=is_closing,
                home=_odds_decimal(cells, columns[0]),
                draw=_odds_decimal(cells, columns[1]),
                away=_odds_decimal(cells, columns[2]),
            )
        )
    return quotes
