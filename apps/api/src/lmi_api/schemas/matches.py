"""Match payloads.

A fixture has two levels of detail. :class:`MatchSummary` is what a collection
returns: the fixture itself, both teams and the result once it is known.
:class:`MatchDetail` adds :class:`MatchStats`, the per-team statistics of the
match, and is what a single-match request returns.

Every nullable field means "not known yet", never "zero": a match that has not
been played carries no goals, no half-time score and no result, and a match
whose statistics were never loaded has no statistics row at all, which is why
``stats`` is ``None`` rather than an object full of nulls. The same holds inside
:class:`MatchStats`: one missing column is ``None``, and the client renders it
as unknown.
"""

import datetime
from typing import Literal

from pydantic import BaseModel

from lmi_api.schemas.common import Page
from lmi_api.schemas.teams import Team

MatchStatus = Literal["scheduled", "played"]
"""The two states a stored fixture can be in.

``scheduled`` means the match has not been played: the score, the half-time
score and the result are all unknown. ``played`` means they are known, and the
result is consistent with the score.
"""

MatchOutcome = Literal["H", "D", "A"]
"""The 1X2 result of a played match, from the home side's point of view.

``H`` is a home win, ``D`` a draw and ``A`` an away win. The database stores the
same letters in a one-character column, and the type is repeated here so the
generated client models them as an enumeration instead of a free string.
"""


class MatchSummary(BaseModel):
    """One fixture as a collection returns it.

    ``season`` is the season code, not its identifier, because that is the code
    a client passes back as a filter. The two teams are nested objects rather
    than identifiers: a list of fixtures is rendered with the club names, and
    nesting them costs nothing because the endpoint reads them from the same
    query. ``kickoff_time`` is ``None`` when the source did not publish a time.
    """

    id: int
    season: str
    kickoff_date: datetime.date
    kickoff_time: datetime.time | None
    matchday: int | None
    status: MatchStatus
    home_team: Team
    away_team: Team
    home_goals: int | None
    away_goals: int | None
    ht_home_goals: int | None
    ht_away_goals: int | None
    result: MatchOutcome | None


class MatchStats(BaseModel):
    """The per-team statistics of one match.

    One field per source column, so a client renders what is stored without
    guessing. The expected goals are floats: the database keeps them as a
    decimal with two places, but a client computes with them, and a decimal
    serialised as a string would force it to parse them first.
    """

    home_shots: int | None
    away_shots: int | None
    home_shots_on_target: int | None
    away_shots_on_target: int | None
    home_fouls: int | None
    away_fouls: int | None
    home_corners: int | None
    away_corners: int | None
    home_yellow: int | None
    away_yellow: int | None
    home_red: int | None
    away_red: int | None
    home_xg: float | None
    away_xg: float | None


class MatchDetail(MatchSummary):
    """One fixture with its statistics.

    ``stats`` is ``None`` when the match has no statistics row, which is the
    normal state of a fixture that has not been played yet. It is not an object
    with only null fields: the client needs to know whether statistics exist at
    all before it renders a section for them.
    """

    stats: MatchStats | None


class MatchPage(Page[MatchSummary]):
    """One page of fixtures.

    The generic envelope is specialised here so the generated client and the
    OpenAPI document name a concrete schema, ``MatchPage``, instead of an
    anonymous generic.
    """
