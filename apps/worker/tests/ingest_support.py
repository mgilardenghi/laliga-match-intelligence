"""Builders and fakes shared by the ingestion integration tests.

The builders produce the two source formats from canonical team names, so a test
can write a fixture list and the matching season file without duplicating the
alias table. The alias lookup goes through the packaged CSV, which is exactly the
map the database seed uses, so a team that resolves in a test also resolves in a
deployed database.

The openfootball builder also writes a kickoff ``time`` and a ``score`` block.
The parser must ignore both: football-data wins the time and the result, and a
fixture list that carried them would still contribute only dates and matchdays.

The football-data builder always writes an odds price of ``2.0625`` and, when
enabled, an expected-goals value of ``1.225``. Both sit exactly on a rounding
boundary, so a second identical run proves that the loader's ``ROUND_HALF_UP``
quantization does not report a phantom change.
"""

import csv
import io
import json
from collections.abc import Mapping, Sequence
from datetime import date, time, timedelta

from lmi_core.db.seed import load_alias_rows

_FD_STATS_COLUMNS = (
    "HS",
    "AS",
    "HST",
    "AST",
    "HF",
    "AF",
    "HC",
    "AC",
    "HY",
    "AY",
    "HR",
    "AR",
)
"""The optional statistics columns the builder fills in."""

_FD_ODDS_COLUMNS = (
    "B365H",
    "B365D",
    "B365A",
    "PSH",
    "PSD",
    "PSA",
    "AvgH",
    "AvgD",
    "AvgA",
    "AvgCH",
    "AvgCD",
    "AvgCA",
)
"""One complete column group per canonical slot: bet365, pinnacle, average."""

_FD_ODDS_VALUES = {
    "B365H": "2.0625",
    "B365D": "3.4",
    "B365A": "4.2",
    "PSH": "2.05",
    "PSD": "3.5",
    "PSA": "4.0",
    "AvgH": "2.1",
    "AvgD": "3.3",
    "AvgA": "4.1",
    "AvgCH": "2.0",
    "AvgCD": "3.4",
    "AvgCA": "4.3",
}
"""The price of every odds column; all are greater than one."""

_FD_STATS_VALUES = {
    "HS": "10",
    "AS": "8",
    "HST": "5",
    "AST": "3",
    "HF": "12",
    "AF": "14",
    "HC": "6",
    "AC": "4",
    "HY": "1",
    "AY": "2",
    "HR": "0",
    "AR": "0",
}
"""A plausible, valid set of match statistics."""


def canonical_teams() -> list[str]:
    """Return the canonical names of the 20 teams that have an openfootball alias."""
    return sorted({row.team for row in load_alias_rows() if row.source == "openfootball"})


def aliases_of(source: str) -> dict[str, str]:
    """Return canonical team name to the alias of one source."""
    return {row.team: row.alias for row in load_alias_rows() if row.source == source}


def round_robin(team_names: Sequence[str]) -> list[list[tuple[str, str]]]:
    """Return the matchdays of a double round robin built with the circle method.

    The first half is one single round robin and the second half is its mirror,
    so every ordered pair of teams plays once. The result is a list of matchdays;
    each matchday holds ``len(team_names) // 2`` fixtures.

    Raises:
        ValueError: the list has fewer than two teams or an odd number of them.
    """
    names = list(team_names)
    if len(names) < 2 or len(names) % 2 != 0:
        raise ValueError(f"a round robin needs an even number of at least two teams: {len(names)}")
    fixed = names[0]
    rotating = names[1:]
    rounds: list[list[tuple[str, str]]] = []
    for _ in range(len(names) - 1):
        arrangement = [fixed, *rotating]
        pairs: list[tuple[str, str]] = []
        for position in range(len(names) // 2):
            pairs.append((arrangement[position], arrangement[len(names) - 1 - position]))
        rounds.append(pairs)
        rotating = rotating[1:] + rotating[:1]
    return rounds + [[(away, home) for home, away in pairs] for pairs in rounds]


def flatten(rounds: Sequence[Sequence[tuple[str, str]]]) -> list[tuple[str, str]]:
    """Return every fixture of ``rounds``, matchday after matchday."""
    return [pair for matchday in rounds for pair in matchday]


def openfootball_json(
    team_names: Sequence[str],
    *,
    start: date = date(2026, 8, 15),
    date_overrides: Mapping[tuple[str, str], date] | None = None,
) -> bytes:
    """Return a complete openfootball fixture list for ``team_names``.

    ``team_names`` are canonical names; the builder maps them onto the
    openfootball aliases of the packaged alias table, exactly as the database
    seed does. ``date_overrides`` moves the date of the canonical pairs it names
    and leaves every other fixture on its matchday's weekly date. Each entry also
    carries a ``time`` and a ``score`` that the parser must ignore.
    """
    aliases = aliases_of("openfootball")
    overrides = {} if date_overrides is None else date_overrides
    matches: list[dict[str, object]] = []
    for index, matchday in enumerate(round_robin(team_names)):
        fixture_date = start + timedelta(days=7 * index)
        for home, away in matchday:
            day = overrides.get((home, away), fixture_date)
            matches.append(
                {
                    "round": f"Matchday {index + 1}",
                    "date": day.isoformat(),
                    "time": "20:00",
                    "team1": aliases[home],
                    "team2": aliases[away],
                    "score": {"ft": [2, 1], "ht": [1, 0]},
                }
            )
    document = {"name": "La Liga", "matches": matches}
    return json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")


def football_data_csv(
    matches: Sequence[tuple[str, str]],
    *,
    start: date = date(2026, 8, 15),
    time_value: time = time(21, 0),
    include_time: bool = True,
    include_stats: bool = True,
    include_xg: bool = False,
    include_odds: bool = True,
    bom: bool = False,
    crlf: bool = False,
    raw_names: Mapping[str, str] | None = None,
) -> bytes:
    """Return a football-data season file of played matches.

    ``matches`` are canonical team names; the builder maps them onto the
    football-data aliases of the packaged alias table. ``raw_names`` replaces the
    alias of the teams it names, which is how a test writes a name that the alias
    table must reject.

    Every row is a home win with the same score and the same statistics. The
    optional switches add the vintage columns of the season files: a byte order
    mark, CRLF line endings, the ``Time`` column, statistics, ``HxG``/``AxG`` and
    the B365, PS, Avg and AvgC odds families.
    """
    aliases = aliases_of("football_data")
    overrides = {} if raw_names is None else raw_names
    header = ["Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]
    if include_time:
        header.append("Time")
    if include_stats:
        header.extend(_FD_STATS_COLUMNS)
    if include_xg:
        header.extend(("HxG", "AxG"))
    if include_odds:
        header.extend(_FD_ODDS_COLUMNS)

    rows: list[dict[str, str]] = []
    for index, (home, away) in enumerate(matches):
        day = start + timedelta(days=3 * index)
        row: dict[str, str] = {
            "Div": "SP1",
            "Date": day.strftime("%d/%m/%Y"),
            "HomeTeam": overrides.get(home, aliases[home]),
            "AwayTeam": overrides.get(away, aliases[away]),
            "FTHG": "2",
            "FTAG": "1",
            "FTR": "H",
        }
        if include_time:
            row["Time"] = time_value.strftime("%H:%M")
        if include_stats:
            row.update(_FD_STATS_VALUES)
        if include_xg:
            row["HxG"] = "1.225"
            row["AxG"] = "0.875"
        if include_odds:
            row.update(_FD_ODDS_VALUES)
        rows.append(row)

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=header,
        restval="",
        lineterminator="\r\n" if crlf else "\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    text = ("\ufeff" if bom else "") + buffer.getvalue()
    return text.encode("utf-8")


class FakeFetcher:
    """A fetcher that serves a fixed mapping and records every request.

    An unknown URL fails the test instead of reaching the network, so a plan that
    asks for an unexpected file is visible as an assertion rather than a live
    request.
    """

    def __init__(self, mapping: Mapping[str, bytes | Exception]) -> None:
        self._mapping = dict(mapping)
        self.requested: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.requested.append(url)
        try:
            body = self._mapping[url]
        except KeyError:
            raise AssertionError(f"unexpected URL in the test fetcher: {url}") from None
        if isinstance(body, Exception):
            raise body
        return body
