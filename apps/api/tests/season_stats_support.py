"""Synthetic match statistics for the season-stats endpoint tests.

Everything inserted here is invented: the shot, foul, card and expected-goals
numbers are made up, nothing is downloaded or ingested, and no source is
contacted. The helper covers the one row the resource reads on top of the
fixtures of :mod:`tests.factories`, which are reused rather than duplicated.

The row is stored through the ``Session`` it is given and flushed, never
committed: the caller's transaction decides whether it survives, and the flush
is enough for the endpoint to select it. Every column defaults to ``None``,
which is the schema's "not known" and what a source publishes when it has no
value for a match; a test sets only the columns its assertion needs.
"""

from decimal import Decimal

from lmi_core.db.models import Match, MatchStat
from sqlalchemy.orm import Session


def create_match_stats(
    session: Session,
    *,
    match: Match,
    home_shots: int | None = None,
    away_shots: int | None = None,
    home_shots_on_target: int | None = None,
    away_shots_on_target: int | None = None,
    home_fouls: int | None = None,
    away_fouls: int | None = None,
    home_corners: int | None = None,
    away_corners: int | None = None,
    home_yellow: int | None = None,
    away_yellow: int | None = None,
    home_red: int | None = None,
    away_red: int | None = None,
    home_xg: float | None = None,
    away_xg: float | None = None,
) -> MatchStat:
    """Insert the statistics row of ``match`` and return the ORM row.

    The columns are split by side, exactly as the table stores them, so a caller
    passes the home value and the away value of each statistic. A value left at
    ``None`` is stored as null and stays out of the corresponding average
    instead of counting as zero.

    The expected goals are written as decimals at the column's scale, so a
    caller passes a float but the stored row carries the value the numeric
    column keeps.
    """
    row = MatchStat(
        match_id=match.id,
        home_shots=home_shots,
        away_shots=away_shots,
        home_shots_on_target=home_shots_on_target,
        away_shots_on_target=away_shots_on_target,
        home_fouls=home_fouls,
        away_fouls=away_fouls,
        home_corners=home_corners,
        away_corners=away_corners,
        home_yellow=home_yellow,
        away_yellow=away_yellow,
        home_red=home_red,
        away_red=away_red,
        home_xg=None if home_xg is None else Decimal(str(home_xg)),
        away_xg=None if away_xg is None else Decimal(str(away_xg)),
    )
    session.add(row)
    session.flush()
    return row
