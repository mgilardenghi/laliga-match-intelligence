"""Tests for :mod:`lmi_core.db.seed`.

Only the pure parts are covered here: season derivation, name normalization and
alias CSV validation. The database behaviour of :func:`lmi_core.db.seed.seed`
(insert-missing, an idempotent second run, a rejected remap) needs a live
PostgreSQL and is covered by the integration tier.
"""

from pathlib import Path

import pytest
from lmi_core.db.seed import AliasRow, expected_seasons, load_alias_rows, normalize_name

HEADER = "source,alias,team\n"


def _write_csv(tmp_path: Path, *rows: str) -> Path:
    """Write an alias CSV from ``rows`` and return its path."""
    path = tmp_path / "team_aliases.csv"
    path.write_text(HEADER + "".join(row + "\n" for row in rows), encoding="utf-8")
    return path


def test_expected_seasons_covers_2005_to_2027() -> None:
    seasons = expected_seasons()

    assert len(seasons) == 22
    assert seasons[0].code == "2005-06"
    assert seasons[0].start_year == 2005
    assert seasons[0].fd_code == "0506"
    assert seasons[-1].code == "2026-27"
    assert seasons[-1].start_year == 2026
    assert seasons[-1].fd_code == "2627"


def test_expected_seasons_has_no_duplicate_codes() -> None:
    seasons = expected_seasons()

    assert [season.start_year for season in seasons] == list(range(2005, 2027))
    assert len({season.code for season in seasons}) == len(seasons)
    assert len({season.fd_code for season in seasons}) == len(seasons)


def test_normalize_name_collapses_whitespace() -> None:
    assert normalize_name("  Atlético \t Madrid \n") == "Atlético Madrid"
    assert normalize_name("") == ""
    assert normalize_name("   ") == ""


def test_normalize_name_keeps_accents_and_composes_them() -> None:
    assert normalize_name("Málaga") == "Málaga"
    assert normalize_name("Atle\u0301tico Madrid") == "Atlético Madrid"
    assert normalize_name("Atlético Madrid") != normalize_name("Atletico Madrid")


def test_load_alias_rows_returns_normalized_rows(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        " football_data , Ath Madrid , Atlético  Madrid ",
        "openfootball,Atletico de Madrid,Atlético Madrid",
    )

    assert load_alias_rows(path) == [
        AliasRow(source="football_data", alias="Ath Madrid", team="Atlético Madrid"),
        AliasRow(source="openfootball", alias="Atletico de Madrid", team="Atlético Madrid"),
    ]


@pytest.mark.parametrize(
    "header",
    ["source,alias,club", "source,alias", "alias,source,team", "Source,Alias,Team"],
)
def test_load_alias_rows_rejects_a_bad_header(tmp_path: Path, header: str) -> None:
    path = tmp_path / "team_aliases.csv"
    path.write_text(header + "\nfootball_data,Ath Madrid,Atlético Madrid\n", encoding="utf-8")

    with pytest.raises(ValueError, match="header"):
        load_alias_rows(path)


def test_load_alias_rows_rejects_an_unknown_source(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, "transfermarkt,Ath Madrid,Atlético Madrid")

    with pytest.raises(ValueError) as error:
        load_alias_rows(path)

    assert "transfermarkt" in str(error.value)


def test_load_alias_rows_rejects_an_empty_alias(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, "football_data,   ,Atlético Madrid")

    with pytest.raises(ValueError) as error:
        load_alias_rows(path)

    assert "empty alias" in str(error.value)


def test_load_alias_rows_rejects_an_empty_team(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, "football_data,Ath Madrid,  ")

    with pytest.raises(ValueError) as error:
        load_alias_rows(path)

    assert "empty team" in str(error.value)


def test_load_alias_rows_rejects_a_conflicting_duplicate(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "football_data,Ath Madrid,Atlético Madrid",
        "football_data, Ath Madrid ,Athletic Club",
    )

    with pytest.raises(ValueError) as error:
        load_alias_rows(path)

    message = str(error.value)
    assert "Ath Madrid" in message
    assert "Atlético Madrid" in message
    assert "Athletic Club" in message


def test_load_alias_rows_collapses_an_exact_duplicate(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "football_data,Ath Madrid,Atlético Madrid",
        " football_data ,Ath Madrid,Atlético  Madrid",
    )

    assert load_alias_rows(path) == [
        AliasRow(source="football_data", alias="Ath Madrid", team="Atlético Madrid")
    ]
