"""Synthetic market prices for the live-metrics endpoint tests.

Everything inserted here is invented: the prices, the bookmaker and the market
are made up, nothing is downloaded or ingested, and no source is contacted. The
helpers only cover what this endpoint needs on top of the live fixtures, which
live in :mod:`tests.live_support` and are reused rather than duplicated.

The price written here is a pre-closing 1x2 row, the only kind the
played-match loader accepts for the market reference. It is stored through the
``Session`` it is given and flushed, never committed: the caller's transaction
decides whether the row survives, and the flush is enough for the loader to
select it.
"""

from decimal import Decimal

from lmi_core.db.models import Match, MatchOdd
from sqlalchemy.orm import Session


def store_odds(
    session: Session,
    *,
    match: Match,
    bookmaker: str = "pinnacle",
    home: float = 2.0,
    draw: float = 3.5,
    away: float = 4.0,
    is_closing: bool = False,
    market: str = "1x2",
) -> MatchOdd:
    """Insert one 1x2 price for ``match`` and return the ORM row.

    The defaults are an open pre-closing Pinnacle price, which is the first
    bookmaker in the loader's priority and therefore the row the market
    reference reads. ``is_closing`` defaults to false because a closing price is
    only known at kickoff and the loader deliberately drops it.

    The prices are written as decimals at the column's scale, so a caller passes
    a float but the stored row carries the same value the numeric column keeps.
    """
    row = MatchOdd(
        match_id=match.id,
        bookmaker=bookmaker,
        market=market,
        is_closing=is_closing,
        home=Decimal(str(home)),
        draw=Decimal(str(draw)),
        away=Decimal(str(away)),
    )
    session.add(row)
    session.flush()
    return row
