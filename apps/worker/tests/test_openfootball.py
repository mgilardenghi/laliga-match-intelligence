"""Tests for the openfootball fixture-list parser.

The fixture is hand-written and synthetic: the four clubs are invented and no real
fixture list is copied. The inline documents carry the malformed cases, so no
broken JSON has to be stored on disk.
"""

import json
from datetime import date
from itertools import permutations
from pathlib import Path

import pytest
from lmi_worker.sources.openfootball import OfFixture, OfSnapshotError, parse_json

FIXTURES = Path(__file__).parent / "fixtures"

_TEAMS = ("Alpha FC", "Beta CF", "Gamma CD", "Delta SC")

# The fixture file is a 4-team double round robin over six matchdays.
_EXPECTED = [
    OfFixture(date=date(2026, 8, 15), matchday=1, team1="Alpha FC", team2="Beta CF"),
    OfFixture(date=date(2026, 8, 16), matchday=1, team1="Gamma CD", team2="Delta SC"),
    OfFixture(date=date(2026, 8, 22), matchday=2, team1="Alpha FC", team2="Gamma CD"),
    OfFixture(date=date(2026, 8, 23), matchday=2, team1="Beta CF", team2="Delta SC"),
    OfFixture(date=date(2026, 8, 29), matchday=3, team1="Alpha FC", team2="Delta SC"),
    OfFixture(date=date(2026, 8, 30), matchday=3, team1="Beta CF", team2="Gamma CD"),
    OfFixture(date=date(2026, 9, 12), matchday=4, team1="Beta CF", team2="Alpha FC"),
    OfFixture(date=date(2026, 9, 13), matchday=4, team1="Delta SC", team2="Gamma CD"),
    OfFixture(date=date(2026, 9, 19), matchday=5, team1="Gamma CD", team2="Alpha FC"),
    OfFixture(date=date(2026, 9, 20), matchday=5, team1="Delta SC", team2="Beta CF"),
    OfFixture(date=date(2026, 9, 26), matchday=6, team1="Delta SC", team2="Alpha FC"),
    OfFixture(date=date(2026, 9, 27), matchday=6, team1="Gamma CD", team2="Beta CF"),
]


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _document(*matches: tuple[int, str, str, str]) -> bytes:
    """Encode ``(matchday, date, team1, team2)`` tuples as an openfootball file."""
    payload = {
        "name": "Synthetic Primera Division",
        "matches": [
            {"round": f"Matchday {matchday}", "date": when, "team1": team1, "team2": team2}
            for matchday, when, team1, team2 in matches
        ],
    }
    return json.dumps(payload).encode("utf-8")


def _two_team_snapshot() -> bytes:
    """A complete 2-team double round robin: two fixtures over two matchdays."""
    return _document(
        (1, "2026-08-15", "Alpha FC", "Beta CF"),
        (2, "2026-08-22", "Beta CF", "Alpha FC"),
    )


def test_2026_27_parses_a_full_double_round_robin() -> None:
    fixtures = parse_json(_fixture_bytes("of_2026_27.json"))

    assert fixtures == _EXPECTED
    assert sorted({fixture.matchday for fixture in fixtures}) == [1, 2, 3, 4, 5, 6]
    teams = {fixture.team1 for fixture in fixtures} | {fixture.team2 for fixture in fixtures}
    assert teams == set(_TEAMS)


def test_every_matchday_holds_two_fixtures_and_every_pair_plays_twice() -> None:
    fixtures = parse_json(_fixture_bytes("of_2026_27.json"))

    for matchday in range(1, 7):
        assert len([fixture for fixture in fixtures if fixture.matchday == matchday]) == 2
    assert {(fixture.team1, fixture.team2) for fixture in fixtures} == set(permutations(_TEAMS, 2))


def test_time_and_score_do_not_change_the_result() -> None:
    document = json.loads(_fixture_bytes("of_2026_27.json"))
    without_them = {
        "matches": [
            {key: value for key, value in match.items() if key not in ("time", "score")}
            for match in document["matches"]
        ]
    }

    assert parse_json(json.dumps(without_them).encode("utf-8")) == _EXPECTED


def test_an_absurd_score_is_ignored() -> None:
    document = json.loads(_fixture_bytes("of_2026_27.json"))
    for match in document["matches"]:
        match["score"] = {"ft": [99, 99], "ht": [14, -1]}

    assert parse_json(json.dumps(document).encode("utf-8")) == _EXPECTED


def test_team_names_are_nfc_with_collapsed_whitespace() -> None:
    # U+0301 after "o" must compose into the precomposed "Atención".
    content = _document(
        (1, "2026-08-15", "  Alpha   FC ", "Atencio\u0301n  FC"),
        (2, "2026-08-22", "Atención FC", "\tAlpha FC\n"),
    )

    fixtures = parse_json(content)

    assert [(fixture.team1, fixture.team2) for fixture in fixtures] == [
        ("Alpha FC", "Atención FC"),
        ("Atención FC", "Alpha FC"),
    ]


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b'{"matches": [', "not valid JSON"),
        (b'[{"round": "Matchday 1"}]', "JSON object"),
        (b'{"name": "Primera Division"}', "no matches field"),
        (b'{"matches": []}', "is empty"),
        (
            _document(
                (1, "2026-08-15", "Alpha FC", "Beta CF"),
                (2, "2026-08-22", "  Alpha   FC ", "Beta CF"),
            ),
            "duplicate",
        ),
        (
            # Three teams need six fixtures; five is one short.
            _document(
                (1, "2026-08-15", "Alpha FC", "Beta CF"),
                (2, "2026-08-22", "Gamma CD", "Alpha FC"),
                (3, "2026-08-29", "Beta CF", "Gamma CD"),
                (4, "2026-09-12", "Beta CF", "Alpha FC"),
                (4, "2026-09-13", "Gamma CD", "Beta CF"),
            ),
            "double round robin of 3 teams has 6",
        ),
        (
            # Six fixtures over four matchdays: one matchday has 2, another has 1.
            _document(
                (1, "2026-08-15", "Alpha FC", "Beta CF"),
                (1, "2026-08-16", "Gamma CD", "Alpha FC"),
                (2, "2026-08-22", "Beta CF", "Gamma CD"),
                (3, "2026-08-29", "Beta CF", "Alpha FC"),
                (3, "2026-08-30", "Gamma CD", "Beta CF"),
                (4, "2026-09-12", "Alpha FC", "Gamma CD"),
            ),
            "matchday",
        ),
        (
            # A 3-team list cannot fill four matchdays evenly, here 2-2-1-1.
            _document(
                (1, "2026-08-15", "Alpha FC", "Beta CF"),
                (1, "2026-08-16", "Gamma CD", "Alpha FC"),
                (2, "2026-08-22", "Beta CF", "Gamma CD"),
                (2, "2026-08-23", "Beta CF", "Alpha FC"),
                (3, "2026-08-29", "Gamma CD", "Beta CF"),
                (4, "2026-09-12", "Alpha FC", "Gamma CD"),
            ),
            "every matchday needs exactly 1 fixtures",
        ),
        (
            b'{"matches": [{"round": "Quarterfinal", "date": "2026-08-15",'
            b' "team1": "Alpha FC", "team2": "Beta CF"}]}',
            "round",
        ),
        (
            # Two teams need matchdays 1 and 2; both fixtures on matchday 1 is not enough.
            _document(
                (1, "2026-08-15", "Alpha FC", "Beta CF"),
                (1, "2026-08-22", "Beta CF", "Alpha FC"),
            ),
            "have no fixtures",
        ),
        (
            _document(
                (1, "2026-08-15", "Alpha FC", "Beta CF"),
                (2, "2026-02-30", "Beta CF", "Alpha FC"),
            ),
            "is not a real calendar date",
        ),
        (
            _document(
                (1, "2026-08-15", "Alpha FC", "Beta CF"),
                (2, "15/08/2026", "Beta CF", "Alpha FC"),
            ),
            "ISO",
        ),
        (
            _document(
                (1, "2026-08-15", "Alpha FC", "Beta CF"),
                (2, "2026-08-22", "   ", "Alpha FC"),
            ),
            "empty",
        ),
        (
            _document(
                (1, "2026-08-15", "Alpha FC", "Beta CF"),
                (2, "2026-08-22", "Beta CF", ""),
            ),
            "empty",
        ),
    ],
    ids=[
        "invalid-json",
        "top-level-array",
        "no-matches-field",
        "empty-matches",
        "duplicate-pair",
        "one-fixture-short",
        "uneven-matchdays",
        "three-teams-uneven",
        "cup-round",
        "missing-matchday",
        "impossible-date",
        "non-iso-date",
        "empty-team1",
        "empty-team2",
    ],
)
def test_rejects_an_unusable_snapshot(content: bytes, reason: str) -> None:
    with pytest.raises(OfSnapshotError, match=reason):
        parse_json(content)


def test_a_two_team_list_parses() -> None:
    # The smallest usable list: both matchdays of a 2-team double round robin.
    fixtures = parse_json(_two_team_snapshot())

    assert [(fixture.matchday, fixture.team1, fixture.team2) for fixture in fixtures] == [
        (1, "Alpha FC", "Beta CF"),
        (2, "Beta CF", "Alpha FC"),
    ]
