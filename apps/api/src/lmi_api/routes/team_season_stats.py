"""Team season-statistics endpoint: one team's season as a single read.

The resource answers what a season table cannot: how one club earned its row.
The played matches, the statistics rows and the pre-closing prices are stored;
the averages, the venue and tier records, the rolling form and the expected
points are derived on read.

No rule of its own is implemented here. The ranking comes from
:func:`lmi_core.standings.compute_standings`, the averages, counters and rolling
form from :func:`lmi_core.analytics.season_stats.summarise`, the venue and tier
records from the same module's ``match_record``, and the margin-removed
probabilities from :func:`lmi_core.modeling.market.remove_margin`, which is the
only margin removal in the repository. This module only decides which rows the
read selects and how it composes the payload.

The endpoint is served on the ``/teams`` prefix and under the ``teams`` tag of
the team resource, so the documentation groups it with the entity it describes
and a generated client sees one resource group per entity rather than one per
path shape. The route only nests deeper; ``teams.py`` is not edited.
"""

import datetime
import decimal
from collections.abc import Mapping, Sequence
from typing import Final, NamedTuple

import numpy as np
from fastapi import APIRouter, HTTPException
from lmi_core.analytics.season_stats import (
    MatchRecord,
    PlayedMatch,
    SideLine,
    StatValues,
    match_record,
    summarise,
)
from lmi_core.analytics.season_stats import StatLine as CoreStatLine
from lmi_core.analytics.season_stats import Venue as CoreVenue
from lmi_core.db.models import Match as MatchModel
from lmi_core.db.models import MatchOdd as MatchOddModel
from lmi_core.db.models import MatchStat as MatchStatModel
from lmi_core.db.models import Team as TeamModel
from lmi_core.live.data import ODDS_PRIORITY
from lmi_core.modeling.market import remove_margin
from lmi_core.standings import StandingRow, compute_standings
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from lmi_api import views
from lmi_api.db import SessionDep
from lmi_api.errors import PROBLEM_RESPONSES
from lmi_api.routes.teams import team_not_found
from lmi_api.schemas.team_season_stats import TeamSeasonStats
from lmi_api.schemas.teams import Team

router = APIRouter(prefix="/teams", tags=["teams"])

_ONE_X_TWO: Final[str] = "1x2"
"""The market of a 1x2 price, the only market this resource prices a match with."""

_WIN_POINTS: Final[int] = 3
_DRAW_POINTS: Final[int] = 1
"""The points an outcome earns, used only to turn margin-removed prices into xPts."""


class _PlayedRow(NamedTuple):
    """One played match of the season, as the season-wide statement returns it.

    The read selects the four columns the count needs plus the kickoff date the
    chronological order needs, so nothing is loaded that no rule reads.
    """

    id: int
    home_team_id: int
    away_team_id: int
    home_goals: int
    away_goals: int
    kickoff_date: datetime.date


@router.get("/{team_id}/season-stats", responses=PROBLEM_RESPONSES)
def get_team_season_stats(
    team_id: int, session: SessionDep, season: str | None = None
) -> TeamSeasonStats:
    """Return the season statistics of one team in one season.

    ``team`` is the stored club and ``season`` the resolved season code, so a
    client can pass the code back to every other endpoint. The team has to be
    part of the season's team set, which is every club that appears as the home
    or the away side in any match of the season; a club of another season names
    nothing this season can describe.

    ``played`` and ``points`` are the counters of that team set's table row,
    read from :func:`lmi_core.standings.compute_standings` rather than recounted
    here, so the standings endpoint stays the owner of the table and a team can
    never read one total here and another there. ``per_match`` is the team's own
    and its opponents' per-match average over exactly those played matches, and
    ``league_avg`` is the same average over both sides of every played match of
    the season, which is the comparison baseline the payload promises.

    ``xpts_market`` sums the expected points of the matches that carry one, and
    ``xpts_coverage`` counts them. The expected points of a match are
    ``3 * p_home + p_draw`` when the team was the home side and
    ``3 * p_away + p_draw`` when it was the visitor, where the three
    probabilities are :func:`lmi_core.modeling.market.remove_margin` over the
    one eligible price row. A fixture without an eligible pre-closing price has
    null expected points and is left out of the sum and of the counter, rather
    than contributing a zero.

    The price is always *pre-closing*. The eligible rows are the ``1x2`` prices
    with ``is_closing`` false from a bookmaker of
    :data:`lmi_core.live.data.ODDS_PRIORITY`, and the selected row per match is
    the most trusted of them, by the priority rank the live loader and the match
    preview also apply. A closing price is never read, whatever its bookmaker:
    it is only known at kickoff. All prices of the team's matches are read in
    one statement, and because that statement cannot cap one row per match
    without dropping matches, the first eligible row per fixture is kept in
    Python after the rows are ordered.

    ``stat_coverage`` counts the matches that carry a statistics row and
    ``xg_coverage`` the matches that carry an expected-goals value on either
    side. A played match without a statistics row is normal and stays absent:
    it contributes its goals to the goals average and nothing to the other
    fields, so a missing row never becomes a row of zeros. The same rule holds
    inside a row: one missing column is null and is left out of that field's
    mean only.

    ``splits`` is the season by venue, ``home`` counting the matches the team
    played as the home side and ``away`` the ones it played as the visitor. Both
    records come from the same ``match_record`` rule as the tiers, so the two
    views cannot disagree on what a win is worth.

    ``tiers`` groups the team's matches by the opponent's position in the season
    table. With ``n`` teams the top tier is positions ``1..ceil(n/3)``, the
    middle the next ``ceil(n/3)`` and the bottom the rest; each record carries
    the counters of every match against a club of that tier, whatever the venue,
    and the tier's ``team_ids`` ascending. ``team_ids`` is the membership of the
    tier, not only the opponents this team has already met.

    ``matches`` is the chronological season, ordered by kickoff date and then
    match identifier exactly as ``summarise`` orders it. Each row nests the
    fixture summary and adds the venue, the points, the expected points, the
    venue-scoped goals, the shots of both sides and the rolling form over the at
    most five matches immediately before it. The first row's rolling form is
    therefore all null: a window over no match has no average.

    Every optional field means "not known", never zero. An average over an
    empty set is null, a statistic no counted match carries is null, and a
    group that played no match has a null ``points_per_game``. The coverage
    counters are what lets a client tell an average of one match from an average
    of six.

    The request issues at most eight statements whatever the size of the season:
    the team, the season resolution, the two of
    :func:`lmi_api.views.load_season_standings` (the team set and the counted
    results), the season's played matches, their statistics rows, the
    pre-closing prices and the match summaries of the rows the payload lists. A
    season without a played match of the team skips the statistics, price and
    summary reads and issues fewer, never more.

    The return annotation is the response model, so no ``response_model`` is
    passed.

    Raises:
        HTTPException: 404 when no stored team has ``team_id``, when ``season``
            names no stored season, when no season has a match while ``season``
            is absent, or when the team is not a side of any match of the
            resolved season. All four details are fixed sentences and never
            repeat the value the client sent.
    """
    stored_team = session.scalar(select(TeamModel).where(TeamModel.id == team_id))
    if stored_team is None:
        raise team_not_found(team_id)
    team = Team.model_validate(stored_team)

    season_id, season_code = views.resolve_season(session, code=season)
    teams, results = views.load_season_standings(session, season_id=season_id)
    if team_id not in teams:
        raise HTTPException(status_code=404, detail="Team not found in season")

    standing_rows = compute_standings(teams, results)
    played_rows = _load_played_rows(session, season_id=season_id)
    stats_by_match = _load_stats(session, match_ids=[row.id for row in played_rows])

    team_rows = [row for row in played_rows if team_id in (row.home_team_id, row.away_team_id)]
    prices = _pre_closing_prices(session, match_ids=[row.id for row in team_rows])
    team_matches = [
        _played_match(
            row,
            team_id=team_id,
            stat=stats_by_match.get(row.id),
            price=prices.get(row.id),
        )
        for row in team_rows
    ]
    valuation = summarise(
        team_matches,
        league=_league_lines(played_rows, stats_by_match=stats_by_match),
    )

    summaries = views.load_match_summaries(session, [row.match_id for row in valuation.matches])
    return TeamSeasonStats.model_validate(
        {
            "team": team,
            "season": season_code,
            "played": valuation.played,
            "points": valuation.points,
            "xpts_market": valuation.xpts_market,
            "xpts_coverage": valuation.xpts_coverage,
            "per_match": {
                "for": _stat_fields(valuation.per_match_for),
                "against": _stat_fields(valuation.per_match_against),
            },
            "league_avg": _stat_fields(valuation.league_avg),
            "stat_coverage": valuation.stat_coverage,
            "xg_coverage": valuation.xg_coverage,
            "splits": {
                "home": _split_fields(match_record(_by_venue(team_matches, "home"))),
                "away": _split_fields(match_record(_by_venue(team_matches, "away"))),
            },
            "tiers": _tiers(team_matches, teams=teams, standing_rows=standing_rows),
            "matches": [
                {
                    "match": summaries[row.match_id],
                    "venue": row.venue,
                    "points": row.points,
                    "xpts": row.xpts,
                    "goals_for": row.goals_for,
                    "goals_against": row.goals_against,
                    "shots_for": row.shots_for,
                    "shots_against": row.shots_against,
                    "rolling5": {
                        "points_pg": row.rolling5.points_pg,
                        "xpts_pg": row.rolling5.xpts_pg,
                        "goals_for_pg": row.rolling5.goals_for_pg,
                        "goals_against_pg": row.rolling5.goals_against_pg,
                    },
                }
                for row in valuation.matches
            ],
        }
    )


def _load_played_rows(session: Session, *, season_id: int) -> list[_PlayedRow]:
    """Return the played matches of one season in one statement.

    A played row always carries both goal columns, which the ``played_complete``
    check constraint enforces, so the filter states the rule and the conversion
    narrows what it guarantees. Nothing else is selected: the identifiers are
    what the team filter and the statistics join need, and the date is what the
    chronological order needs.
    """
    statement = select(
        MatchModel.id,
        MatchModel.home_team_id,
        MatchModel.away_team_id,
        MatchModel.home_goals,
        MatchModel.away_goals,
        MatchModel.kickoff_date,
    ).where(
        MatchModel.season_id == season_id,
        MatchModel.status == "played",
        MatchModel.home_goals.is_not(None),
        MatchModel.away_goals.is_not(None),
    )
    return [
        _PlayedRow(
            id=int(row.id),
            home_team_id=int(row.home_team_id),
            away_team_id=int(row.away_team_id),
            home_goals=int(row.home_goals),
            away_goals=int(row.away_goals),
            kickoff_date=row.kickoff_date,
        )
        for row in session.execute(statement).all()
    ]


def _load_stats(session: Session, *, match_ids: Sequence[int]) -> dict[int, MatchStatModel]:
    """Return the statistics rows of ``match_ids`` in one statement, keyed by match.

    A played match without a row is simply absent from the mapping, which is the
    normal state of a fixture whose source published no statistics; no row of
    nulls is invented for it.
    """
    if not match_ids:
        return {}
    return {
        stat.match_id: stat
        for stat in session.scalars(
            select(MatchStatModel).where(MatchStatModel.match_id.in_(match_ids))
        )
    }


def _pre_closing_prices(
    session: Session, *, match_ids: Sequence[int]
) -> dict[int, tuple[float, float, float]]:
    """Return the most trusted pre-closing ``1x2`` price of each requested match.

    The eligible rows are the pre-closing ``1x2`` prices of a bookmaker of
    :data:`lmi_core.live.data.ODDS_PRIORITY`, ordered by match and then by the
    priority rank, so the first row of a match is the one the live loader and
    the match preview would also select. A closing price is excluded in SQL,
    whatever its bookmaker: it is only known at kickoff.

    One statement serves every match. The statement cannot use ``LIMIT 1`` per
    match without dropping the other matches, so the first eligible row of each
    match is kept in Python, and a later row of the same match is ignored
    because the order already put the most trusted first. A fixture with no
    eligible price is absent from the mapping.

    Args:
        session: a session bound to the application database.
        match_ids: the fixtures to price. An empty sequence issues no statement.

    Returns:
        The ``(home, draw, away)`` decimal prices of each priceable match, by
        match identifier.
    """
    if not match_ids:
        return {}
    priority = case(
        *((MatchOddModel.bookmaker == name, rank) for rank, name in enumerate(ODDS_PRIORITY)),
        else_=len(ODDS_PRIORITY),
    )
    statement = (
        select(
            MatchOddModel.match_id,
            MatchOddModel.home,
            MatchOddModel.draw,
            MatchOddModel.away,
        )
        .where(
            MatchOddModel.match_id.in_(match_ids),
            MatchOddModel.is_closing.is_(False),
            MatchOddModel.market == _ONE_X_TWO,
            MatchOddModel.bookmaker.in_(ODDS_PRIORITY),
        )
        .order_by(MatchOddModel.match_id, priority)
    )
    prices: dict[int, tuple[float, float, float]] = {}
    for match_id, home, draw, away in session.execute(statement).all():
        prices.setdefault(int(match_id), (float(home), float(draw), float(away)))
    return prices


def _played_match(
    row: _PlayedRow,
    *,
    team_id: int,
    stat: MatchStatModel | None,
    price: tuple[float, float, float] | None,
) -> PlayedMatch:
    """Return one played match seen from ``team_id``'s side.

    The stored home/away score and the two statistics rows are flipped when the
    team was the visitor, so every field of the result is venue-scoped. The
    expected points are null when ``price`` is null and are never derived from a
    closing row, because no closing row reaches this function.
    """
    at_home = row.home_team_id == team_id
    venue: CoreVenue = "home" if at_home else "away"
    return PlayedMatch(
        match_id=row.id,
        kickoff_date=row.kickoff_date,
        venue=venue,
        opponent_id=row.away_team_id if at_home else row.home_team_id,
        goals_for=row.home_goals if at_home else row.away_goals,
        goals_against=row.away_goals if at_home else row.home_goals,
        xpts=_expected_points(venue, price),
        stats_for=_stat_values(stat, side="home" if at_home else "away"),
        stats_against=_stat_values(stat, side="away" if at_home else "home"),
    )


def _expected_points(venue: CoreVenue, price: tuple[float, float, float] | None) -> float | None:
    """Return the expected points a margin-removed price implies, or ``None`` without a price.

    A win is worth three points and a draw one, so the expectation is
    ``3 * p_win + p_draw`` from the team's venue. The margin removal is the
    core rule and is not restated here.
    """
    if price is None:
        return None
    implied = remove_margin(np.array([list(price)], dtype=np.float64))[0]
    if venue == "home":
        return _WIN_POINTS * float(implied[0]) + _DRAW_POINTS * float(implied[1])
    return _WIN_POINTS * float(implied[2]) + _DRAW_POINTS * float(implied[1])


def _stat_values(stat: MatchStatModel | None, *, side: CoreVenue) -> StatValues | None:
    """Return one side's statistics of a match, or ``None`` when no row exists.

    A missing column stays null inside the returned object, so the caller's
    average leaves that field out instead of counting a zero. The expected goals
    are a decimal column and become floats because the payload computes with
    them.
    """
    if stat is None:
        return None
    if side == "home":
        return StatValues(
            shots=stat.home_shots,
            shots_on_target=stat.home_shots_on_target,
            fouls=stat.home_fouls,
            corners=stat.home_corners,
            yellow=stat.home_yellow,
            red=stat.home_red,
            xg=_float_or_none(stat.home_xg),
        )
    return StatValues(
        shots=stat.away_shots,
        shots_on_target=stat.away_shots_on_target,
        fouls=stat.away_fouls,
        corners=stat.away_corners,
        yellow=stat.away_yellow,
        red=stat.away_red,
        xg=_float_or_none(stat.away_xg),
    )


def _league_lines(
    rows: Sequence[_PlayedRow], *, stats_by_match: Mapping[int, MatchStatModel]
) -> list[SideLine]:
    """Return both sides of every played match as the league-wide baseline.

    The list is twice the number of matches, because a league average counts the
    home line and the away line of each fixture separately; a match without a
    statistics row still contributes its two goal lines.
    """
    lines: list[SideLine] = []
    for row in rows:
        stat = stats_by_match.get(row.id)
        lines.append(SideLine(goals=row.home_goals, stats=_stat_values(stat, side="home")))
        lines.append(SideLine(goals=row.away_goals, stats=_stat_values(stat, side="away")))
    return lines


def _by_venue(matches: Sequence[PlayedMatch], venue: CoreVenue) -> list[PlayedMatch]:
    """Return the matches ``venue`` selects, keeping the caller's order."""
    return [match for match in matches if match.venue == venue]


def _tiers(
    matches: Sequence[PlayedMatch],
    *,
    teams: Mapping[int, str],
    standing_rows: Sequence[StandingRow],
) -> dict[str, object]:
    """Return the three tier payloads of one team's season.

    The tier boundaries follow the payload contract: with ``n`` teams the top
    tier holds positions ``1..ceil(n/3)``, the middle the next ``ceil(n/3)`` and
    the bottom the rest. The position of every club comes from
    :func:`lmi_core.standings.compute_standings`, and a match counts in the tier
    of its opponent, whatever the venue. The membership list is ascending so
    two reads of one season serve the same order.
    """
    position = {row.team_id: row.position for row in standing_rows}
    third = -(-len(teams) // 3)
    memberships = {
        "top": sorted(team_id for team_id in teams if position[team_id] <= third),
        "middle": sorted(team_id for team_id in teams if third < position[team_id] <= 2 * third),
        "bottom": sorted(team_id for team_id in teams if position[team_id] > 2 * third),
    }
    return {
        tier: _tier_fields(matches, member_ids=member_ids)
        for tier, member_ids in memberships.items()
    }


def _tier_fields(matches: Sequence[PlayedMatch], *, member_ids: Sequence[int]) -> dict[str, object]:
    """Return one tier record as the mapping the payload validation reads.

    ``member_ids`` is both the membership the counters filter on and the
    ascending identifier list the payload carries, so the two cannot drift.
    """
    members = set(member_ids)
    record = match_record(match for match in matches if match.opponent_id in members)
    return {**_split_fields(record), "team_ids": list(member_ids)}


def _split_fields(record: MatchRecord) -> dict[str, int | float | None]:
    """Return the counters of a record as the mapping ``SplitRecord`` validates."""
    return {
        "played": record.played,
        "won": record.won,
        "drawn": record.drawn,
        "lost": record.lost,
        "goals_for": record.goals_for,
        "goals_against": record.goals_against,
        "points": record.points,
        "points_per_game": record.points_per_game,
    }


def _stat_fields(line: CoreStatLine) -> dict[str, float | None]:
    """Return one core average line as the mapping ``StatLine`` validates."""
    return {
        "goals": line.goals,
        "shots": line.shots,
        "shots_on_target": line.shots_on_target,
        "fouls": line.fouls,
        "corners": line.corners,
        "yellow": line.yellow,
        "red": line.red,
        "xg": line.xg,
    }


def _float_or_none(value: decimal.Decimal | None) -> float | None:
    """Return a decimal column as a float, keeping a missing value null."""
    return None if value is None else float(value)
