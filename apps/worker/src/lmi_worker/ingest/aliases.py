"""Resolve source team names to database team ids.

A name is matched exactly after :func:`lmi_core.db.seed.normalize_name`, which
applies Unicode NFC and collapses whitespace. There is no case folding, no
abbreviation expansion and no fuzzy matching: a near miss must be added to the
alias CSV by hand, because a guessed match would attach one club's matches to
another club and the mistake would look like real data.

The alias table is loaded per source, so the same spelling can belong to
different clubs in different sources without ever colliding.
"""

from collections.abc import Iterable, Mapping

from lmi_core.db.models import TeamAlias
from lmi_core.db.seed import normalize_name
from sqlalchemy import select
from sqlalchemy.orm import Session


class UnknownTeamNames(ValueError):
    """Every source name of one snapshot that the alias table does not know.

    The message is part of the contract: the ingestion runner stores it verbatim
    as the error of a failed run, so the names are sorted, quoted and separated
    by commas.
    """

    def __init__(self, source: str, names: Iterable[str]) -> None:
        self.source = source
        self.names: tuple[str, ...] = tuple(sorted(set(names)))
        listed = ", ".join(repr(name) for name in self.names)
        super().__init__(f"unknown {source} team name(s): {listed}")


class AliasResolver:
    """The alias table of one source, ready to resolve names to team ids."""

    def __init__(self, source: str, by_name: Mapping[str, int]) -> None:
        self.source = source
        self._by_name = dict(by_name)

    @classmethod
    def load(cls, session: Session, source: str) -> AliasResolver:
        """Read every alias of ``source`` from the database."""
        rows = session.execute(
            select(TeamAlias.alias, TeamAlias.team_id).where(TeamAlias.source == source)
        )
        return cls(source, {normalize_name(alias): team_id for alias, team_id in rows})

    def resolve(self, names: Iterable[str]) -> dict[str, int]:
        """Return the team id of every name, keyed by the name as given.

        Matching normalizes both sides exactly as the seed stores them.

        Raises:
            UnknownTeamNames: at least one name is unknown. The error lists
                every unknown name at once, so one run reports the whole gap in
                the alias table instead of only the first name.
        """
        resolved: dict[str, int] = {}
        unknown: list[str] = []
        for name in names:
            normalized = normalize_name(name)
            team_id = self._by_name.get(normalized)
            if team_id is None:
                unknown.append(normalized)
            else:
                resolved[name] = team_id
        if unknown:
            raise UnknownTeamNames(self.source, unknown)
        return resolved
