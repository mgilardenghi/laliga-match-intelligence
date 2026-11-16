"""Season-statistics payloads: one team's season as averages, tiers and match rows.

The team and the fixture summaries are stored rows served as they are. Every
other value is derived on read, and no rule of it is restated here: the averages,
the counters, the expected-points total and the rolling form come from
:func:`lmi_core.analytics.season_stats.summarise`, the venue and tier records
from :func:`lmi_core.analytics.season_stats.match_record`, and the tier
membership from :func:`lmi_core.standings.compute_standings`, the only ranking in
the codebase. A correction to one of those rules therefore reaches this resource
without a change to these shapes.

Two nulls carry meaning and neither means "zero". A statistic no counted match
carries is null, never ``0.0``, so a team whose rows never recorded expected
goals has an unknown xG average while a team whose rows all say zero has an
average of zero. ``xpts_market`` is null when no match of the season carried an
eligible pre-closing price, and ``xpts_coverage`` says how many matches the sum
rests on; the same reading applies to ``stat_coverage`` and ``xg_coverage`` on
the statistics side. ``points_per_game`` of a group that played nothing is null
because an average over no match does not exist.

The ``for`` side of :class:`PerMatch` is the literal JSON key the contract
promises, while the Python attribute is ``for_`` because ``for`` is a keyword.
The field carries the alias, and the payload is built through
``model_validate`` with the alias keys, the same technique
:class:`~lmi_api.schemas.predictions.PredictionDetail` uses for its
``model_config`` field.
"""

from typing import Literal

from pydantic import BaseModel, Field

from lmi_api.schemas.matches import MatchSummary
from lmi_api.schemas.team_focus import SplitRecord, TeamSplits
from lmi_api.schemas.teams import Team

__all__ = [
    "PerMatch",
    "Rolling5",
    "SeasonMatchLine",
    "SeasonTiers",
    "StatLine",
    "TeamSeasonStats",
    "TierRecord",
    "Venue",
]

Venue = Literal["home", "away"]
"""Where the team played a match: ``"home"`` or ``"away"``."""


class StatLine(BaseModel):
    """A per-match average line of one side, every field optional.

    ``goals`` is averaged over every counted match; each other field is averaged
    over only the matches that carry it, so the denominators can differ by
    field. A null field means the counted matches hold no value for it, which a
    client renders as unknown.
    """

    goals: float | None
    shots: float | None
    shots_on_target: float | None
    fouls: float | None
    corners: float | None
    yellow: float | None
    red: float | None
    xg: float | None


class PerMatch(BaseModel):
    """The team's own and its opponents' per-match averages over the season.

    ``for_`` is the team's line and ``against`` the line of the clubs it faced.
    The two are kept apart so a team that wins without creating chances is
    distinguishable from one that creates without winning. The JSON key of the
    first side is ``for``, which is why the attribute is ``for_``.
    """

    for_: StatLine = Field(alias="for")
    against: StatLine


class Rolling5(BaseModel):
    """Averages over the at most five matches immediately before one match.

    The window never includes the match it describes, so the line answers what
    the team did before kickoff. Every field is null when the window is empty,
    which is the team's first match of the season.
    """

    points_pg: float | None
    xpts_pg: float | None
    goals_for_pg: float | None
    goals_against_pg: float | None


class SeasonMatchLine(BaseModel):
    """One played match of the season, seen from the team's side.

    ``match`` is the fixture summary, nested so a client renders the clubs, the
    date and the score without a second request. ``shots_for`` and
    ``shots_against`` are the shots columns only, and are null when that side of
    the match has no statistics row or no shots value.
    """

    match: MatchSummary
    venue: Venue
    points: int
    xpts: float | None
    goals_for: int
    goals_against: int
    shots_for: int | None
    shots_against: int | None
    rolling5: Rolling5


class TierRecord(SplitRecord):
    """The record of a team's matches against one tier of the season table.

    The counters are the same shape as a venue split and cover every match
    against a club of that tier, whatever the venue. ``team_ids`` is the
    membership of the tier, ascending: the clubs that occupy those table
    positions, not only the opponents this team has already met.
    """

    team_ids: list[int]


class SeasonTiers(BaseModel):
    """The team's season split by the tier of the opponent it faced.

    With ``n`` teams in the season the top tier is positions ``1..ceil(n/3)``,
    the middle the next ``ceil(n/3)`` and the bottom the rest, so the three
    records partition the table exactly. A table of fewer than five clubs can
    leave the bottom tier empty -- with ``n = 4``, ``ceil(n/3) = 2`` fills the
    top and the middle and leaves nothing for the bottom -- and an empty tier is
    served as a record of zeros with a null ``points_per_game`` and an empty
    ``team_ids`` rather than being dropped.
    """

    top: TierRecord
    middle: TierRecord
    bottom: TierRecord


class TeamSeasonStats(BaseModel):
    """Everything one team's season serves: averages, tiers and one row per match.

    ``season`` is the season code the other endpoints accept as their filter.
    ``played`` and ``points`` are the table's counters over the same played
    matches, ``per_match`` and ``league_avg`` are the season's own and the
    league-wide per-match averages, and the coverage counters say how many
    matches each derived number rests on. ``splits`` covers the season by venue
    and ``tiers`` by the opponent's table position, while ``matches`` is the
    chronological season the rolling form of each row is computed from.
    """

    team: Team
    season: str
    played: int
    points: int
    xpts_market: float | None
    xpts_coverage: int
    per_match: PerMatch
    league_avg: StatLine
    stat_coverage: int
    xg_coverage: int
    splits: TeamSplits
    tiers: SeasonTiers
    matches: list[SeasonMatchLine]
