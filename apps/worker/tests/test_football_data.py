"""Tests for the football-data.co.uk season parser.

Every fixture is hand-written and synthetic: the club names are invented and no
real source file is copied. The inline bytes literals carry the encoding cases,
the row rejections and the whole-file errors, so the encoding tests never depend
on line endings surviving git.
"""

from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
from lmi_worker.sources.football_data import (
    FdRejection,
    FdSnapshotError,
    OddsQuote,
    parse_csv,
)

FIXTURES = Path(__file__).parent / "fixtures"

_HEADER = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HS,AS"
_ODDS_HEADER = f"{_HEADER},B365H,B365D,B365A"
_GOOD_ROW = "SP1,12/09/20,Alpha FC,Beta CF,2,0,H,1,0,10,8"
_GOOD_ROW_WITH_ODDS = f"{_GOOD_ROW},1.90,3.50,4.20"

# A 2026/27 shaped file: BOM plus CRLF endings, both supplied by the test itself.
_BOM_CSV = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HS,AS,HST,AST,HF,AF,HC,AC,"
    "HY,AY,HR,AR,HxG,AxG,B365H,B365D,B365A,B365CH,B365CD,B365CA,PPH,PPD,PPA,PPCH,PPCD,PPCA\r\n"
    "SP1,22/08/2026,21:00,Alpha FC,Beta CF,2,0,H,1,0,13,9,6,3,11,14,7,4,1,2,0,0,1.42,0.35,"
    "1.75,3.60,4.50,1.70,3.70,4.80,1.78,3.65,4.55,1.72,3.75,4.85\r\n"
)


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _csv(header: str, *rows: str) -> bytes:
    return "\r\n".join((header, *rows)).encode("utf-8") + b"\r\n"


def _slots(quotes: list[OddsQuote]) -> set[tuple[str, bool]]:
    return {(quote.bookmaker, quote.is_closing) for quote in quotes}


def _rejection_of(good_row: str, bad_row: str, *, header: str = _HEADER) -> FdRejection:
    """Parse one good row followed by one bad row and return the single rejection."""
    snapshot = parse_csv(_csv(header, good_row, bad_row))
    assert len(snapshot.matches) == 1
    [rejection] = snapshot.rejections
    assert rejection.row_number == 3
    return rejection


def test_2005_06_reads_two_digit_dates_and_opening_odds_only() -> None:
    snapshot = parse_csv(_fixture_bytes("fd_2005_06.csv"))

    assert snapshot.rejections == []
    assert len(snapshot.matches) == 3
    first = snapshot.matches[0]
    assert first.date == date(2005, 8, 27)
    assert first.time is None
    assert (first.home, first.away) == ("Alpha FC", "Beta CF")
    assert (first.fthg, first.ftag, first.ftr) == (2, 1, "H")
    assert (first.hthg, first.htag) == (1, 0)
    assert first.stats.home_shots == 12
    assert first.stats.away_red == 0
    assert first.home_xg is None
    assert [
        (quote.bookmaker, quote.is_closing, quote.home, quote.away) for quote in first.odds
    ] == [
        ("bet365", False, Decimal("1.85"), Decimal("3.90")),
        ("market_avg", False, Decimal("1.95"), Decimal("3.85")),
        ("market_max", False, Decimal("2.05"), Decimal("4.00")),
    ]


def test_2015_16_maps_pinnacle_opening_and_closing() -> None:
    snapshot = parse_csv(_fixture_bytes("fd_2015_16.csv"))

    assert snapshot.rejections == []
    match = snapshot.matches[0]
    assert match.date == date(2015, 8, 22)
    quotes = {(quote.bookmaker, quote.is_closing): quote for quote in match.odds}
    assert quotes[("pinnacle", False)].home == Decimal("1.85")
    assert quotes[("pinnacle", False)].draw == Decimal("3.60")
    assert quotes[("pinnacle", False)].away == Decimal("4.40")
    assert quotes[("pinnacle", True)].home == Decimal("1.80")
    assert quotes[("pinnacle", True)].away == Decimal("4.60")
    # That vintage has no B365C columns, so bet365 is opening only.
    assert quotes[("bet365", False)].home == Decimal("1.90")
    assert ("bet365", True) not in quotes


def test_2018_19_reads_four_digit_years() -> None:
    snapshot = parse_csv(_fixture_bytes("fd_2018_19.csv"))

    assert snapshot.rejections == []
    assert [match.date for match in snapshot.matches] == [
        date(2018, 8, 17),
        date(2018, 8, 31),
        date(2018, 9, 15),
    ]
    assert [match.time for match in snapshot.matches] == [None, None, None]


def test_2021_22_reads_kickoff_times_and_the_market_families() -> None:
    snapshot = parse_csv(_fixture_bytes("fd_2021_22.csv"))

    assert snapshot.rejections == []
    match = snapshot.matches[0]
    assert match.time == time(21, 0)
    assert [later.time for later in snapshot.matches[1:]] == [time(19, 30), time(16, 15)]
    quotes = {(quote.bookmaker, quote.is_closing): quote for quote in match.odds}
    assert quotes[("market_avg", False)].draw == Decimal("3.65")
    assert quotes[("market_avg", True)].away == Decimal("4.90")
    assert quotes[("market_max", False)].home == Decimal("1.95")
    assert quotes[("market_max", True)].home == Decimal("1.85")
    assert quotes[("bet365", True)].away == Decimal("4.80")


@pytest.mark.parametrize(
    ("name", "expected_slots"),
    [
        (
            "fd_2005_06.csv",
            {("bet365", False), ("market_avg", False), ("market_max", False)},
        ),
        (
            "fd_2015_16.csv",
            {
                ("bet365", False),
                ("pinnacle", False),
                ("pinnacle", True),
                ("market_avg", False),
                ("market_max", False),
            },
        ),
        (
            "fd_2018_19.csv",
            {
                ("bet365", False),
                ("bet365", True),
                ("pinnacle", False),
                ("market_avg", False),
                ("market_max", False),
            },
        ),
        (
            "fd_2021_22.csv",
            {
                ("bet365", False),
                ("bet365", True),
                ("market_avg", False),
                ("market_avg", True),
                ("market_max", False),
                ("market_max", True),
            },
        ),
        (
            "fd_2026_27.csv",
            {("bet365", False), ("bet365", True), ("pinnacle", False), ("pinnacle", True)},
        ),
    ],
)
def test_a_vintage_only_reports_the_odds_families_it_has(
    name: str, expected_slots: set[tuple[str, bool]]
) -> None:
    snapshot = parse_csv(_fixture_bytes(name))

    assert snapshot.rejections == []
    assert len(snapshot.matches) >= 2
    for match in snapshot.matches:
        # One quote per slot at most, and no quote for an absent family.
        assert _slots(match.odds) == expected_slots
        assert len(match.odds) == len(expected_slots)


def test_2026_27_reads_expected_goals_and_pinnacle_groups() -> None:
    snapshot = parse_csv(_fixture_bytes("fd_2026_27.csv"))

    assert snapshot.rejections == []
    match = snapshot.matches[0]
    assert match.time == time(21, 0)
    assert (match.home_xg, match.away_xg) == (Decimal("1.42"), Decimal("0.35"))
    quotes = {(quote.bookmaker, quote.is_closing): quote for quote in match.odds}
    assert quotes[("pinnacle", False)].home == Decimal("1.78")
    assert quotes[("pinnacle", True)].home == Decimal("1.72")
    assert quotes[("bet365", True)].away == Decimal("4.85")


def test_bom_and_crlf_bytes_parse() -> None:
    # A byte order mark left on the first header name would hide the core column
    # "Div", so a successful parse already proves the BOM was dropped.
    snapshot = parse_csv(b"\xef\xbb\xbf" + _BOM_CSV.encode("utf-8"))

    assert snapshot.rejections == []
    [match] = snapshot.matches
    assert match.date == date(2026, 8, 22)
    assert match.time == time(21, 0)
    assert (match.home_xg, match.away_xg) == (Decimal("1.42"), Decimal("0.35"))
    quotes = {(quote.bookmaker, quote.is_closing): quote for quote in match.odds}
    assert quotes[("pinnacle", False)].home == Decimal("1.78")
    assert quotes[("pinnacle", False)].away == Decimal("4.55")
    assert quotes[("pinnacle", True)].home == Decimal("1.72")
    assert quotes[("pinnacle", True)].away == Decimal("4.85")


def test_team_names_are_nfc_with_collapsed_whitespace() -> None:
    # "Atencio" + U+0301 must become the precomposed "Atención".
    row = "SP1,12/09/20,  Atencio\u0301n   FC ,Beta\tCF,1,0,H,0,0,9,7"

    snapshot = parse_csv(_csv(_HEADER, row))

    [match] = snapshot.matches
    assert match.home == "Atención FC"
    assert match.away == "Beta CF"


def test_trailing_blank_rows_are_skipped() -> None:
    content = _csv(_HEADER, _GOOD_ROW) + b",,,,,\r\n\r\n"

    snapshot = parse_csv(content)

    assert len(snapshot.matches) == 1
    assert snapshot.rejections == []


@pytest.mark.parametrize("odds", ["1.90,3.50,", "1.90,,4.20", ",3.50,4.20"])
def test_a_partially_filled_odds_group_is_omitted(odds: str) -> None:
    snapshot = parse_csv(_csv(_ODDS_HEADER, f"{_GOOD_ROW},{odds}"))

    [match] = snapshot.matches
    assert match.odds == []
    assert snapshot.rejections == []


def test_a_second_quote_for_the_same_slot_is_rejected() -> None:
    header = f"{_HEADER},PSH,PSD,PSA,PPH,PPD,PPA"
    row = "SP1,12/09/20,Alpha FC,Beta CF,2,0,H,1,0,10,8,1.85,3.60,4.40,1.80,3.70,4.60"

    snapshot = parse_csv(_csv(header, row))

    assert snapshot.matches == []
    [rejection] = snapshot.rejections
    assert rejection.row_number == 2
    assert "ambiguous" in rejection.reason


def test_a_row_with_extra_fields_is_a_rejection() -> None:
    snapshot = parse_csv(_csv(_HEADER, _GOOD_ROW, f"{_GOOD_ROW},surplus"))

    assert len(snapshot.matches) == 1
    [rejection] = snapshot.rejections
    assert rejection.row_number == 3
    assert "header" in rejection.reason


def test_a_truncated_row_is_a_rejection_not_a_crash() -> None:
    snapshot = parse_csv(_csv(_HEADER, _GOOD_ROW, "SP1,12/09/20,Gamma CD"))

    assert len(snapshot.matches) == 1
    [rejection] = snapshot.rejections
    assert rejection.row_number == 3
    assert "AwayTeam" in rejection.reason
    assert rejection.raw["Div"] == "SP1"
    assert rejection.raw["FTHG"] == ""


def test_rejects_a_result_that_disagrees_with_the_score() -> None:
    rejection = _rejection_of(_GOOD_ROW, "SP1,12/09/20,Gamma CD,Alpha FC,2,0,A,1,0,10,8")

    assert "FTR" in rejection.reason
    assert rejection.raw["HomeTeam"] == "Gamma CD"


@pytest.mark.parametrize(
    ("home", "away"),
    [
        # The same name spelled identically, padded and NFC-equivalent.
        ("Alpha FC", "Alpha FC"),
        ("Alpha FC", "  Alpha   FC "),
        ("Atención FC", "Atencio\u0301n FC"),
    ],
)
def test_rejects_a_team_playing_itself(home: str, away: str) -> None:
    rejection = _rejection_of(_GOOD_ROW, f"SP1,12/09/20,{home},{away},2,0,H,1,0,10,8")

    assert "HomeTeam/AwayTeam" in rejection.reason
    assert "cannot play itself" in rejection.reason


@pytest.mark.parametrize(
    ("row", "column"),
    [
        ("SP1,12/09/20,Gamma CD,Alpha FC,1,1,D,2,1,10,8", "HTHG"),
        ("SP1,12/09/20,Gamma CD,Alpha FC,1,1,D,1,2,10,8", "HTAG"),
    ],
)
def test_rejects_half_time_goals_above_the_full_time_score(row: str, column: str) -> None:
    rejection = _rejection_of(_GOOD_ROW, row)

    assert f"{column}:" in rejection.reason
    assert "exceed" in rejection.reason


@pytest.mark.parametrize(
    ("row", "empty_column"),
    [
        ("SP1,12/09/20,Gamma CD,Alpha FC,0,1,A,,1,10,8", "HTHG"),
        ("SP1,12/09/20,Gamma CD,Alpha FC,1,0,H,1,,10,8", "HTAG"),
    ],
)
def test_rejects_a_row_with_only_one_half_time_goal(row: str, empty_column: str) -> None:
    rejection = _rejection_of(_GOOD_ROW, row)

    assert "HTHG/HTAG" in rejection.reason
    assert f"{empty_column} is empty" in rejection.reason


def test_empty_half_time_goals_stay_none() -> None:
    snapshot = parse_csv(_csv(_HEADER, "SP1,12/09/20,Alpha FC,Beta CF,2,0,H,,,10,8"))

    [match] = snapshot.matches
    assert (match.hthg, match.htag) == (None, None)
    assert snapshot.rejections == []


@pytest.mark.parametrize(
    ("odds", "column"),
    [
        (",3.50,n/a", "B365A"),
        ("1.90,,n/a", "B365A"),
        ("n/a,3.50,", "B365H"),
    ],
)
def test_rejects_garbage_odds_in_an_incomplete_group(odds: str, column: str) -> None:
    rejection = _rejection_of(
        _GOOD_ROW_WITH_ODDS,
        f"SP1,12/09/20,Gamma CD,Alpha FC,0,1,A,0,0,10,8,{odds}",
        header=_ODDS_HEADER,
    )

    assert column in rejection.reason


def test_rejects_odds_at_or_below_one_in_an_incomplete_group() -> None:
    rejection = _rejection_of(
        _GOOD_ROW_WITH_ODDS,
        "SP1,12/09/20,Gamma CD,Alpha FC,0,1,A,0,0,10,8,1.90,,1.00",
        header=_ODDS_HEADER,
    )

    assert "B365A" in rejection.reason


def test_rejects_negative_goals() -> None:
    rejection = _rejection_of(_GOOD_ROW, "SP1,12/09/20,Gamma CD,Alpha FC,-1,0,A,0,0,10,8")

    assert "FTHG" in rejection.reason


def test_rejects_an_unparseable_date() -> None:
    rejection = _rejection_of(_GOOD_ROW, "SP1,2020-09-12,Gamma CD,Alpha FC,0,1,A,0,0,10,8")

    assert "Date" in rejection.reason


def test_rejects_a_negative_statistic() -> None:
    rejection = _rejection_of(_GOOD_ROW, "SP1,12/09/20,Gamma CD,Alpha FC,0,1,A,0,0,-2,8")

    assert "HS" in rejection.reason


def test_rejects_odds_not_greater_than_one() -> None:
    rejection = _rejection_of(
        _GOOD_ROW_WITH_ODDS,
        "SP1,12/09/20,Gamma CD,Alpha FC,0,1,A,0,0,10,8,1.90,1.00,4.20",
        header=_ODDS_HEADER,
    )

    assert "B365D" in rejection.reason


def test_rejects_non_numeric_odds() -> None:
    rejection = _rejection_of(
        _GOOD_ROW_WITH_ODDS,
        "SP1,12/09/20,Gamma CD,Alpha FC,0,1,A,0,0,10,8,1.90,3.50,n/a",
        header=_ODDS_HEADER,
    )

    assert "B365A" in rejection.reason


def test_rejects_an_empty_body() -> None:
    with pytest.raises(FdSnapshotError):
        parse_csv(b"")


def test_rejects_undecodable_bytes() -> None:
    with pytest.raises(FdSnapshotError, match="UTF-8"):
        parse_csv(b"\xff\xfe\x00Div,Date\r\n")


def test_rejects_a_header_without_data_rows() -> None:
    with pytest.raises(FdSnapshotError, match="no data rows"):
        parse_csv(_csv(_HEADER))
    with pytest.raises(FdSnapshotError, match="no data rows"):
        parse_csv(_csv(_HEADER, ",,,,,"))


def test_rejects_a_header_without_a_core_column() -> None:
    header = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG"

    with pytest.raises(FdSnapshotError, match="FTR"):
        parse_csv(_csv(header, "SP1,12/09/20,Alpha FC,Beta CF,2,0"))


def test_rejects_a_row_outside_the_la_liga_division() -> None:
    with pytest.raises(FdSnapshotError, match="SP2"):
        parse_csv(_csv(_HEADER, _GOOD_ROW, "SP2,12/09/20,Gamma CD,Alpha FC,0,1,A,0,0,10,8"))


def test_rejects_a_duplicate_fixture_pair() -> None:
    later = "SP1,10/01/21,  Alpha   FC ,Beta CF,1,1,D,0,0,9,9"

    with pytest.raises(FdSnapshotError, match="duplicate"):
        parse_csv(_csv(_HEADER, _GOOD_ROW, later))
