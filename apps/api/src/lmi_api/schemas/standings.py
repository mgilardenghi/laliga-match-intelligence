"""Standing payloads.

A standings response is a table plus the two facts a client needs to read it
correctly: which season it covers and up to which date the results were
counted.

:class:`Standings` is deliberately not the core dataclass that
:class:`~lmi_core.standings.StandingRow` computes. The core type is the internal
shape the ranking produces, with a bare team identifier; the payload here nests
the :class:`~lmi_api.schemas.teams.Team` the client renders and turns the form
tuple into a list, so a client links a row to a club without a second request.
The route maps between the two explicitly, which is why a change to one of them
cannot silently change the other.
"""

import datetime
from typing import Literal

from pydantic import BaseModel

from lmi_api.schemas.teams import Team

StandingForm = Literal["W", "D", "L"]
"""One outcome of a team's recent form.

``W`` is a win, ``D`` a draw and ``L`` a loss, all from the point of view of the
team whose row carries it. The values match the ones the core ranking writes
into its form tuple.
"""


class StandingRow(BaseModel):
    """One team's line in the table.

    Every counter covers only the matches the response counted: the played
    matches of the season up to ``as_of``, or all of them when ``as_of`` is
    absent. A team whose fixtures have not been played yet keeps its row, with
    every counter at zero and an empty ``form``, so the table always lists the
    whole season and never hides a club that has not started.

    ``goal_difference`` is ``goals_for - goals_against`` and may be negative.
    ``form`` holds at most the five most recent outcomes, most recent first,
    which is why it can be shorter than ``played``.
    """

    position: int
    team: Team
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int
    form: list[StandingForm]


class Standings(BaseModel):
    """The table of one season, with the scope it was computed for.

    ``season`` is the season code, which is also what a client passes back as
    the ``season`` filter of the other endpoints. ``as_of`` repeats the cutoff
    the request asked for and is ``None`` when the table counts every played
    match of the season. ``matches_counted`` is the number of results that went
    into the counters, so a client can tell an empty table from a table whose
    matchdays have not been played yet.

    ``rows`` is ordered as the competition ranks it, positions ``1..n`` with no
    shared place. The API does not re-sort it.
    """

    season: str
    as_of: datetime.date | None
    matches_counted: int
    rows: list[StandingRow]
