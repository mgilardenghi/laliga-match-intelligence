"""Reference data that no source provides: seasons, teams and the alias map.

The three reference tables are written here and nowhere else. Source loaders
read them, so a spelling that is missing from the alias map stops a load with a
clear error instead of quietly creating a second team.

Team names are matched **exactly after** :func:`normalize_name`: Unicode NFC
plus collapsed whitespace. There is deliberately no fuzzy matching, no
abbreviation expansion and no accent folding. A near miss ("Ath Bilbao" instead
of "Athletic Club") must be added to ``data/team_aliases.csv`` by hand, because
a guessed match would attach one club's matches to another club and the mistake
would look like real data.

The seed is idempotent and never remaps: running it twice inserts nothing, and
an alias that the database already maps to a different team aborts the run
instead of being rewritten.
"""

import csv
import io
import unicodedata
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from lmi_core.db.models import Season, Team, TeamAlias
from lmi_core.db.session import session_factory

SEASON_START_YEARS = range(2005, 2027)
"""First year of every covered season, 2005-06 through 2026-27."""

ALIAS_SOURCES = ("football_data", "openfootball")
"""The sources allowed by the ``ck_team_aliases_source_valid`` check constraint."""

ALIAS_CSV_COLUMNS = ("source", "alias", "team")
"""The exact header of the alias CSV, in order."""

_PACKAGE = "lmi_core"


@dataclass(frozen=True)
class SeasonRow:
    """One season as the seed defines it."""

    code: str
    start_year: int
    fd_code: str


@dataclass(frozen=True)
class AliasRow:
    """One source spelling mapped to one canonical team name."""

    source: str
    alias: str
    team: str


def season_code(start_year: int) -> str:
    """Return the season label of the season that starts in ``start_year``."""
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def fd_code(start_year: int) -> str:
    """Return the football-data URL segment of the season that starts in ``start_year``."""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def expected_seasons() -> list[SeasonRow]:
    """Return every season the project covers, oldest first."""
    return [
        SeasonRow(code=season_code(year), start_year=year, fd_code=fd_code(year))
        for year in SEASON_START_YEARS
    ]


def normalize_name(value: str) -> str:
    """Return the form in which a team name is stored and compared.

    Unicode NFC composition plus collapsed whitespace, so ``"  Atlético
    Madrid "`` and a decomposed ``"Atle\\u0301tico Madrid"`` both become
    ``"Atlético Madrid"``. Nothing else changes: case, punctuation and accents
    are preserved, because the canonical names carry the Spanish spelling.
    """
    return " ".join(unicodedata.normalize("NFC", value).split())


def load_alias_rows(path: Path | None = None) -> list[AliasRow]:
    """Return the alias rows of a CSV file, normalized and validated.

    Args:
        path: the CSV to read. ``None`` reads the copy packaged with
            :mod:`lmi_core`, so the seed also works from the wheel and the
            container image.

    Raises:
        ValueError: the header is not ``source,alias,team``, a source is not
            one of :data:`ALIAS_SOURCES`, an alias or team name is empty after
            normalization, or one ``(source, alias)`` pair maps to two
            different teams. An exact duplicate row is collapsed.
        OSError: the file cannot be read.
    """
    if path is None:
        resource = resources.files(_PACKAGE) / "data" / "team_aliases.csv"
        return parse_alias_rows(resource.read_text(encoding="utf-8"), origin=str(resource))
    return parse_alias_rows(path.read_text(encoding="utf-8"), origin=str(path))


def parse_alias_rows(text: str, *, origin: str) -> list[AliasRow]:
    """Parse and validate alias CSV ``text``, naming ``origin`` in every error."""
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header is None or tuple(cell.strip() for cell in header) != ALIAS_CSV_COLUMNS:
        raise ValueError(f"{origin}: header must be exactly {','.join(ALIAS_CSV_COLUMNS)}")

    rows: dict[tuple[str, str], AliasRow] = {}
    for line_number, record in enumerate(reader, start=2):
        if not any(cell.strip() for cell in record):
            continue
        if len(record) != len(ALIAS_CSV_COLUMNS):
            raise ValueError(
                f"{origin}:{line_number}: expected {len(ALIAS_CSV_COLUMNS)} columns, "
                f"found {len(record)}"
            )
        source, alias, team = (normalize_name(cell) for cell in record)
        if source not in ALIAS_SOURCES:
            raise ValueError(
                f"{origin}:{line_number}: unknown source {source!r}, "
                f"expected one of {', '.join(ALIAS_SOURCES)}"
            )
        if not alias:
            raise ValueError(f"{origin}:{line_number}: empty alias for source {source!r}")
        if not team:
            raise ValueError(f"{origin}:{line_number}: empty team for alias {alias!r}")

        key = (source, alias)
        known = rows.get(key)
        if known is not None and known.team != team:
            raise ValueError(
                f"{origin}:{line_number}: alias {alias!r} of source {source!r} maps to both "
                f"{known.team!r} and {team!r}"
            )
        rows[key] = AliasRow(source=source, alias=alias, team=team)

    return list(rows.values())


def seed(engine: Engine) -> None:
    """Insert the reference data that is missing from the database.

    One transaction covers seasons, teams and aliases, so a rejected remap or a
    failed insert leaves the database unchanged.

    Raises:
        RuntimeError: a stored season or alias contradicts the seed. The alias
            case names the alias, the stored team and the CSV team.
        ValueError: the packaged alias CSV is invalid.
    """
    factories = session_factory(engine)
    rows = load_alias_rows()
    with factories.begin() as session:
        _insert_missing_seasons(session, expected_seasons())
        team_ids = _insert_missing_teams(session, sorted({row.team for row in rows}))
        _insert_missing_aliases(session, rows, team_ids)


def _insert_missing_seasons(session: Session, rows: list[SeasonRow]) -> None:
    """Add the seasons the database lacks; raise if a stored season disagrees."""
    stored = {
        season.start_year: (season.code, season.fd_code)
        for season in session.scalars(select(Season))
    }
    for row in rows:
        known = stored.get(row.start_year)
        if known is None:
            session.add(Season(code=row.code, start_year=row.start_year, fd_code=row.fd_code))
        elif known != (row.code, row.fd_code):
            raise RuntimeError(
                f"season {row.start_year} is stored as {known[0]!r}/{known[1]!r} "
                f"but the seed defines {row.code!r}/{row.fd_code!r}"
            )
    session.flush()


def _insert_missing_teams(session: Session, names: list[str]) -> dict[str, int]:
    """Add the canonical teams the database lacks and return name to id."""
    stored = set(session.scalars(select(Team.name)))
    for name in names:
        if name not in stored:
            session.add(Team(name=name))
    session.flush()
    return {name: team_id for team_id, name in session.execute(select(Team.id, Team.name))}


def _insert_missing_aliases(
    session: Session, rows: list[AliasRow], team_ids: dict[str, int]
) -> None:
    """Add the aliases the database lacks; never remap a stored alias."""
    stored = {
        (alias.source, alias.alias): alias.team_id for alias in session.scalars(select(TeamAlias))
    }
    names = {team_id: name for team_id, name in session.execute(select(Team.id, Team.name))}
    for row in rows:
        team_id = team_ids[row.team]
        known = stored.get((row.source, row.alias))
        if known is None:
            session.add(TeamAlias(team_id=team_id, source=row.source, alias=row.alias))
        elif known != team_id:
            raise RuntimeError(
                f"alias {row.alias!r} of source {row.source!r} is stored for team "
                f"{names.get(known, known)!r} but the CSV maps it to {row.team!r}"
            )
    session.flush()
