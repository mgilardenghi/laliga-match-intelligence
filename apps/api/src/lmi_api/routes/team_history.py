"""Team history endpoint: one club's seasons across every season it played.

The resource answers what one season cannot: how a club moved from one season
to the next. No derived rule is implemented here. The per-season lines and the
steepest declines come from :func:`lmi_core.analytics.history.summarise`, the
expected points from
:func:`lmi_core.analytics.season_stats.market_expected_points`, and the
pre-closing price from :func:`lmi_api.views.load_pre_closing_prices`. The
seasons, their matches and the clubs' names are read through
:func:`lmi_api.views.load_seasons`, :func:`lmi_api.views.load_season_matches`
and :func:`lmi_api.views.load_team_names`, and the table position from
:func:`lmi_api.views.season_table`, which is the same
:func:`lmi_core.standings.compute_standings` call the standings endpoint makes.
This module only decides which rows the read selects and maps them to the
payload.

The resource is the club's whole stored history, so there is no ``season``
parameter: a caller that wants one season asks the season-statistics endpoint
instead. The endpoint is served on the ``/teams`` prefix and under the ``teams``
tag of the team resource, so the documentation groups it with the club it
describes. The route only nests deeper; ``teams.py`` is not edited.
"""

from collections import defaultdict
from collections.abc import Sequence
from typing import NamedTuple, cast

from fastapi import APIRouter
from lmi_core.analytics.history import SeasonInput, SeasonMatch, summarise
from lmi_core.analytics.season_stats import Venue as CoreVenue
from lmi_core.analytics.season_stats import market_expected_points
from lmi_core.db.models import MatchStat as MatchStatModel
from lmi_core.db.models import Team as TeamModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from lmi_api import views
from lmi_api.db import SessionDep
from lmi_api.errors import PROBLEM_RESPONSES
from lmi_api.routes.teams import team_not_found
from lmi_api.schemas.team_history import TeamHistory
from lmi_api.schemas.teams import Team

router = APIRouter(prefix="/teams", tags=["teams"])


class _SideStats(NamedTuple):
    """The statistics of one side of a match, with the club's cards already summed."""

    shots: int | None
    shots_on_target: int | None
    cards: int | None


@router.get("/{team_id}/history", responses=PROBLEM_RESPONSES)
def get_team_history(team_id: int, session: SessionDep) -> TeamHistory:
    """Return the multi-season history of one club.

    ``team`` is the stored club. An identifier no stored club has is a 404; a
    stored club that has played no match at all is a 200 with an empty
    ``seasons`` and an empty ``largest_drop``, because a club without history is
    not an error and a line of zeros would invent a season it never played.

    A season is listed exactly when the club played at least one match in it,
    meaning a match with status ``played`` and both goal columns set. A season in
    which the club only had fixtures, or none at all, is not listed. The order of
    the seasons, the per-field denominators, the null-is-never-zero rule and the
    drop rules are :mod:`lmi_core.analytics.history`'s and are not restated here.

    ``position`` is the club's place in the season's table as
    :func:`lmi_core.standings.compute_standings` ranks it. The ranking receives
    the same two inputs the standings endpoint uses: every club that appears as
    a side of any match of the season, whatever that match's status, and the
    season's played results. Both are read from the one statement that loads the
    seasons' matches, so a history row can never contradict the standings
    endpoint and no season needs a table query of its own.

    Each counted match is the club's side of one played fixture: the stored
    score flipped by venue, the expected points of the one eligible pre-closing
    ``1x2`` price when the fixture has one, and the shots, shots on target and
    cards of each side flipped by venue. ``cards_for`` is the club's own yellow
    plus red and is null only when the row carries neither value, which is why a
    row that records zero keeps zero. A fixture without a statistics row leaves
    every statistics field null, and one missing column stays null without
    zeroing its neighbours. A closing price is never read, whatever its
    bookmaker, because it is only known at kickoff, and a bookmaker outside the
    priority of :data:`lmi_core.live.data.ODDS_PRIORITY` is not eligible.

    The season lines and ``largest_drop`` are the output of
    :func:`lmi_core.analytics.history.summarise` mapped field for field. Nothing
    is rounded, re-sorted or recomputed here.

    The request issues at most six statements whatever the number of seasons and
    matches: the club, the seasons of its played matches, every match of those
    seasons in one statement, the names of those clubs for the ranking's final
    tie-break, the statistics rows of the club's played matches and their
    pre-closing prices. The club's own rows are picked out of the matches
    statement in Python, so no per-season query is issued. A club that played no
    match stops after the first two statements and answers the empty history, so
    the count never grows with the payload.

    The return annotation is the response model, so no ``response_model`` is
    passed.

    Raises:
        HTTPException: 404 when no stored team has ``team_id``.
    """
    stored_team = session.scalar(select(TeamModel).where(TeamModel.id == team_id))
    if stored_team is None:
        raise team_not_found(team_id)
    team = Team.model_validate(stored_team)

    seasons = views.load_seasons(session, team_id=team_id)
    if not seasons:
        return TeamHistory.model_validate({"team": team, "seasons": [], "largest_drop": []})

    matches = views.load_season_matches(session, season_ids=[season.id for season in seasons])
    names = views.load_team_names(
        session,
        team_ids=sorted(
            {side_id for row in matches for side_id in (row.home_team_id, row.away_team_id)}
        ),
    )
    club_rows = [
        row for row in matches if row.played and team_id in (row.home_team_id, row.away_team_id)
    ]
    stats_by_match = _load_stats(session, match_ids=[row.id for row in club_rows])
    prices = views.load_pre_closing_prices(session, match_ids=[row.id for row in club_rows])

    rows_by_season: dict[int, list[views.MatchRow]] = defaultdict(list)
    for row in matches:
        rows_by_season[row.season_id].append(row)

    season_inputs: list[SeasonInput] = []
    for season in seasons:
        table = views.season_table(rows_by_season[season.id], names=names)
        season_inputs.append(
            SeasonInput(
                season=season.code,
                start_year=season.start_year,
                position=next(
                    (standing.position for standing in table if standing.team_id == team_id),
                    None,
                ),
                matches=tuple(
                    _season_match(
                        row,
                        team_id=team_id,
                        stat=stats_by_match.get(row.id),
                        price=prices.get(row.id),
                    )
                    for row in club_rows
                    if row.season_id == season.id
                ),
            )
        )
    history = summarise(season_inputs)
    return TeamHistory.model_validate(
        {
            "team": team,
            "seasons": [
                {
                    "season": line.season,
                    "position": line.position,
                    "played": line.played,
                    "points": line.points,
                    "points_pg": line.points_pg,
                    "goals_for_pg": line.goals_for_pg,
                    "goals_against_pg": line.goals_against_pg,
                    "shots_for_pg": line.shots_for_pg,
                    "shots_against_pg": line.shots_against_pg,
                    "sot_for_pg": line.sot_for_pg,
                    "sot_against_pg": line.sot_against_pg,
                    "cards_pg": line.cards_pg,
                    "xpts_pg": line.xpts_pg,
                }
                for line in history.seasons
            ],
            "largest_drop": [
                {
                    "metric": drop.metric,
                    "from_season": drop.from_season,
                    "to_season": drop.to_season,
                    "delta": drop.delta,
                }
                for drop in history.largest_drop
            ],
        }
    )


def _load_stats(session: Session, *, match_ids: Sequence[int]) -> dict[int, MatchStatModel]:
    """Return the statistics rows of ``match_ids`` in one statement, keyed by match.

    A played match without a row is absent from the mapping, which is the normal
    state of a fixture whose source published no statistics; no row of nulls is
    invented for it.
    """
    if not match_ids:
        return {}
    return {
        stat.match_id: stat
        for stat in session.scalars(
            select(MatchStatModel).where(MatchStatModel.match_id.in_(match_ids))
        )
    }


def _season_match(
    row: views.MatchRow,
    *,
    team_id: int,
    stat: MatchStatModel | None,
    price: tuple[float, float, float] | None,
) -> SeasonMatch:
    """Return one played match seen from the club's side.

    The stored score, the two statistics sides and the expected points are
    flipped by venue, so every value describes the club and not the home side.
    The expected points are null when the fixture has no eligible pre-closing
    price, and no closing row ever reaches this function.
    """
    at_home = row.home_team_id == team_id
    venue: CoreVenue = "home" if at_home else "away"
    own = _side_stats(stat, side=venue)
    other = _side_stats(stat, side="away" if at_home else "home")
    return SeasonMatch(
        goals_for=cast(int, row.home_goals if at_home else row.away_goals),
        goals_against=cast(int, row.away_goals if at_home else row.home_goals),
        xpts=None if price is None else market_expected_points(price, venue=venue),
        shots_for=own.shots,
        shots_against=other.shots,
        sot_for=own.shots_on_target,
        sot_against=other.shots_on_target,
        cards_for=own.cards,
        cards_against=other.cards,
    )


def _side_stats(stat: MatchStatModel | None, *, side: CoreVenue) -> _SideStats:
    """Return one side's statistics of a match, or all-null when no row exists.

    A missing column stays null inside the result, so the season average leaves
    that field out instead of counting a zero. ``cards`` sums the side's yellow
    and red and keeps null only when the row records neither, which is what makes
    a recorded zero distinguishable from an unknown count.
    """
    if stat is None:
        return _SideStats(shots=None, shots_on_target=None, cards=None)
    if side == "home":
        return _SideStats(
            shots=stat.home_shots,
            shots_on_target=stat.home_shots_on_target,
            cards=_cards(stat.home_yellow, stat.home_red),
        )
    return _SideStats(
        shots=stat.away_shots,
        shots_on_target=stat.away_shots_on_target,
        cards=_cards(stat.away_yellow, stat.away_red),
    )


def _cards(yellow: int | None, red: int | None) -> int | None:
    """Return a side's yellow plus red, or ``None`` when the row carries neither.

    A row that records zero in both columns is a counted zero, not an unknown,
    so it must stay distinct from the missing pair.
    """
    if yellow is None and red is None:
        return None
    return (yellow or 0) + (red or 0)
