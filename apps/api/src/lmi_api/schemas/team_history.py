"""Team-history payloads: one club's seasons and its steepest declines.

The seasons and the drops are derived on read, and no rule of them is restated
here: the ordering, the per-field denominators, the null-is-never-zero rule and
the drop rules all live in :mod:`lmi_core.analytics.history`. This module only
names the shapes the endpoint serves.

NULL IS NEVER ZERO. An average that no counted match of a season carries is
``None``, never ``0.0``, so a club whose statistics rows never recorded shots
has an unknown shots average while a club whose rows all record zero has an
average of zero. The same reading governs the drops: an entry exists only when
both seasons carry the metric, because a gap in the source is not a decline on
the pitch. :mod:`lmi_core.analytics.history` is the one place those rules are
decided.
"""

from pydantic import BaseModel

from lmi_api.schemas.teams import Team

__all__ = ["TeamHistory", "TeamHistoryDrop", "TeamHistorySeason"]


class TeamHistorySeason(BaseModel):
    """One season of a club's history, with its table position and per-game averages.

    ``position`` is the club's place in that season's table, ``None`` when the
    table holds no row for it. ``played``, ``points``, ``points_pg``,
    ``goals_for_pg`` and ``goals_against_pg`` are always known for a listed
    season; every other average is ``None`` when no counted match of that season
    carries the value, and is averaged over only the matches that do. Those
    denominators and the null-is-never-zero rule belong to
    :mod:`lmi_core.analytics.history`.
    """

    season: str
    position: int | None
    played: int
    points: int
    points_pg: float
    goals_for_pg: float
    goals_against_pg: float
    shots_for_pg: float | None
    shots_against_pg: float | None
    sot_for_pg: float | None
    sot_against_pg: float | None
    cards_pg: float | None
    xpts_pg: float | None


class TeamHistoryDrop(BaseModel):
    """The steepest decline of one metric between two adjacent seasons.

    ``metric`` is the payload key of the number the entry describes: one of
    ``points_pg``, ``goals_for_pg``, ``goals_against_pg``, ``shots_for_pg``,
    ``shots_against_pg``, ``sot_for_pg``, ``sot_against_pg``, ``cards_pg`` and
    ``xpts_pg``. It stays a plain string because that list is the rule of
    :mod:`lmi_core.analytics.history`; a second copy here would be free to drift
    from it.

    ``delta`` is negative and oriented so it always means the club got worse.
    ``from_season`` is the earlier season and ``to_season`` the later, both taken
    from the history order. A pair with a missing value on either side is
    skipped for that metric, because a gap in the source is not a fall to zero.
    """

    metric: str
    from_season: str
    to_season: str
    delta: float


class TeamHistory(BaseModel):
    """A club's seasons, ascending, and the steepest decline of each metric.

    ``seasons`` lists only the seasons the club played, so a season it did not
    play is absent rather than a line of zeros; a club with no played match has
    an empty list. ``largest_drop`` is empty when nothing declined and when
    fewer than two seasons are listed. Both the order and the emptiness rules
    are :mod:`lmi_core.analytics.history`'s.
    """

    team: Team
    seasons: list[TeamHistorySeason]
    largest_drop: list[TeamHistoryDrop]
