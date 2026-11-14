"""Behavior tests for resolving source team names against an in-memory alias table."""

from collections.abc import Iterator
from typing import cast

import pytest
from lmi_core.db.models import Team, TeamAlias
from lmi_worker.ingest.aliases import AliasResolver, UnknownTeamNames
from sqlalchemy import Engine, Table, create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://")
    cast(Table, Team.__table__).create(engine)
    cast(Table, TeamAlias.__table__).create(engine)
    with Session(engine) as session:
        session.add_all([Team(id=1, name="Atlético Madrid"), Team(id=2, name="Real Betis")])
        session.flush()
        session.add_all(
            [
                TeamAlias(team_id=1, source="football_data", alias="Ath Madrid"),
                TeamAlias(team_id=2, source="football_data", alias="Real Betis"),
                TeamAlias(team_id=1, source="openfootball", alias="Atlético Madrid"),
            ]
        )
        session.commit()
    try:
        yield engine
    finally:
        engine.dispose()


def test_resolve_normalizes_both_sides_exactly(engine: Engine) -> None:
    with Session(engine) as session:
        resolver = AliasResolver.load(session, "openfootball")

    decomposed = "Atle\u0301tico   Madrid"

    assert resolver.resolve([decomposed]) == {decomposed: 1}


def test_resolve_reports_every_unknown_name_sorted(engine: Engine) -> None:
    with Session(engine) as session:
        resolver = AliasResolver.load(session, "football_data")

    with pytest.raises(UnknownTeamNames) as excinfo:
        resolver.resolve(["Zaragoza", "Ath Madrid", "Alavés"])

    error = excinfo.value
    assert error.source == "football_data"
    assert error.names == ("Alavés", "Zaragoza")
    assert str(error) == "unknown football_data team name(s): 'Alavés', 'Zaragoza'"


def test_resolve_is_scoped_to_one_source(engine: Engine) -> None:
    with Session(engine) as session:
        resolver = AliasResolver.load(session, "openfootball")

    with pytest.raises(UnknownTeamNames):
        resolver.resolve(["Ath Madrid"])


def test_resolve_lists_a_repeated_unknown_name_once(engine: Engine) -> None:
    with Session(engine) as session:
        resolver = AliasResolver.load(session, "football_data")

    with pytest.raises(UnknownTeamNames) as excinfo:
        resolver.resolve(["Nope", "Nope", "  Nope "])

    assert excinfo.value.names == ("Nope",)


def test_resolve_keys_the_result_by_the_given_name(engine: Engine) -> None:
    with Session(engine) as session:
        resolver = AliasResolver.load(session, "football_data")

    resolved = resolver.resolve(["Ath  Madrid", "Real Betis"])

    assert resolved == {"Ath  Madrid": 1, "Real Betis": 2}
