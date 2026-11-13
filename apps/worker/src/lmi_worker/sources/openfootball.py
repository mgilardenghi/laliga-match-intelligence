"""Parse the openfootball fixture list into a validated list of fixtures.

The parser is pure: it decodes the bytes of one ``football.json`` fixture list and
returns frozen models. It performs no I/O, touches no database and never maps a
club name onto a canonical club, because the loader owns the alias table.

Only the fixture list is trusted. Kickoff ``time`` and the ``score`` block are
deliberately never read, not even validated, because football-data.co.uk wins the
date and the time and supplies the results; openfootball contributes the fixture
list alone. A file is usable only when it describes a complete double round robin
with an even matchday distribution, so a half-written list is rejected instead of
being silently ingested.
"""

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date as date_type
from datetime import datetime
from typing import cast

from pydantic import BaseModel, ConfigDict

# A round is a league matchday and nothing else: cups and qualifiers are rejected.
_MATCHDAY_PATTERN = re.compile(r"Matchday ([1-9][0-9]*)")
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class OfSnapshotError(ValueError):
    """Raised when the file is not a usable complete fixture list."""


class _FrozenModel(BaseModel):
    """Shared configuration for the immutable fixture models."""

    model_config = ConfigDict(frozen=True)


class OfFixture(_FrozenModel):
    """One fixture: its date and matchday plus the names of both teams.

    The names are normalized to Unicode NFC with single spaces but stay unaliased.
    """

    date: date_type
    matchday: int
    team1: str
    team2: str


def parse_json(content: bytes) -> list[OfFixture]:
    """Parse one openfootball fixture list.

    Raises :class:`OfSnapshotError` when the file as a whole is unusable: not
    UTF-8, not JSON, not an object with a non-empty ``matches`` list, a malformed
    date or round, a duplicate fixture, a fixture count that is not a full double
    round robin, or a matchday range that is not covered evenly.
    """
    document = _document(content)
    raw_matches = document.get("matches")
    if raw_matches is None:
        raise OfSnapshotError("the document has no matches field")
    if not isinstance(raw_matches, list):
        raise OfSnapshotError("the matches field must be a list of matches")
    entries = cast("list[object]", raw_matches)
    if not entries:
        raise OfSnapshotError("the matches list is empty")
    fixtures = [_fixture(entry, number) for number, entry in enumerate(entries, start=1)]
    _reject_duplicate_fixtures(fixtures)
    _validate_fixture_count(fixtures)
    _validate_matchdays(fixtures)
    return fixtures


def _document(content: bytes) -> Mapping[str, object]:
    """Decode and parse the body, which must be a JSON object."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise OfSnapshotError(
            f"the file is not valid UTF-8: {error.reason} at byte {error.start}"
        ) from error
    try:
        document: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise OfSnapshotError(
            f"the file is not valid JSON: {error.msg} at offset {error.pos}"
        ) from error
    if not isinstance(document, dict):
        raise OfSnapshotError(f"the document must be a JSON object, not {type(document).__name__}")
    return cast("dict[str, object]", document)


def _fixture(entry: object, number: int) -> OfFixture:
    """Turn one entry of ``matches`` into a fixture."""
    if not isinstance(entry, dict):
        raise OfSnapshotError(f"match {number} is not a JSON object")
    match = cast("dict[str, object]", entry)
    return OfFixture(
        date=_date(match, number),
        matchday=_matchday(match, number),
        team1=_team(match, "team1", number),
        team2=_team(match, "team2", number),
    )


def _text(match: Mapping[str, object], field: str, number: int) -> str:
    """Read one required string field of one match."""
    if field not in match:
        raise OfSnapshotError(f"match {number}: the {field} field is missing")
    value = match[field]
    if not isinstance(value, str):
        raise OfSnapshotError(f"match {number}: {field} must be a string")
    return value


def _date(match: Mapping[str, object], number: int) -> date_type:
    """Read the ``date`` field, which must be an ISO ``YYYY-MM-DD`` date."""
    text = _text(match, "date", number).strip()
    if _DATE_PATTERN.fullmatch(text) is None:
        raise OfSnapshotError(f"match {number}: date {text!r} is not an ISO YYYY-MM-DD date")
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise OfSnapshotError(
            f"match {number}: date {text!r} is not a real calendar date"
        ) from None


def _matchday(match: Mapping[str, object], number: int) -> int:
    """Read the ``round`` field, which must be exactly ``Matchday N`` with ``N >= 1``."""
    text = _text(match, "round", number).strip()
    matched = _MATCHDAY_PATTERN.fullmatch(text)
    if matched is None:
        raise OfSnapshotError(
            f"match {number}: round {text!r} is not a league matchday of the form Matchday N"
        )
    return int(matched.group(1))


def _team(match: Mapping[str, object], field: str, number: int) -> str:
    """Read a team name, normalized to NFC with single spaces and kept unaliased."""
    normalized = unicodedata.normalize("NFC", _text(match, field, number))
    name = " ".join(normalized.split())
    if not name:
        raise OfSnapshotError(f"match {number}: {field} is empty")
    return name


def _teams(fixtures: Sequence[OfFixture]) -> set[str]:
    """Return the distinct team names of the fixtures."""
    return {fixture.team1 for fixture in fixtures} | {fixture.team2 for fixture in fixtures}


def _reject_duplicate_fixtures(fixtures: Sequence[OfFixture]) -> None:
    """Raise when the same ordered team pair appears twice."""
    seen: set[tuple[str, str]] = set()
    for fixture in fixtures:
        pair = (fixture.team1, fixture.team2)
        if pair in seen:
            raise OfSnapshotError(
                f"duplicate fixture: {pair[0]!r} vs {pair[1]!r} appears more than once"
            )
        seen.add(pair)


def _validate_fixture_count(fixtures: Sequence[OfFixture]) -> None:
    """Raise unless every ordered pair of the ``n`` teams appears exactly once."""
    team_count = len(_teams(fixtures))
    expected = team_count * (team_count - 1)
    if len(fixtures) != expected:
        raise OfSnapshotError(
            f"the file lists {len(fixtures)} fixtures but a double round robin of "
            f"{team_count} teams has {expected}"
        )


def _validate_matchdays(fixtures: Sequence[OfFixture]) -> None:
    """Raise unless matchdays ``1 .. 2*(n-1)`` each hold exactly ``n//2`` fixtures."""
    team_count = len(_teams(fixtures))
    last_matchday = 2 * (team_count - 1)
    per_matchday = team_count // 2
    counts = Counter(fixture.matchday for fixture in fixtures)
    outside = sorted(matchday for matchday in counts if not 1 <= matchday <= last_matchday)
    if outside:
        raise OfSnapshotError(
            f"matchday {outside[0]} is outside 1..{last_matchday}, the range of a double "
            f"round robin of {team_count} teams"
        )
    absent = [str(matchday) for matchday in range(1, last_matchday + 1) if matchday not in counts]
    if absent:
        raise OfSnapshotError(
            f"matchday(s) {', '.join(absent)} have no fixtures; every matchday from 1 to "
            f"{last_matchday} must appear"
        )
    uneven = [
        f"matchday {matchday} has {counts[matchday]}"
        for matchday in range(1, last_matchday + 1)
        if counts[matchday] != per_matchday
    ]
    if uneven:
        raise OfSnapshotError(
            f"every matchday needs exactly {per_matchday} fixtures, but {', '.join(uneven)}"
        )
