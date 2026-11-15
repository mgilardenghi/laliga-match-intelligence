"""Synthetic rows for the API integration tests.

Everything inserted here is invented: the club names, the season codes and the
fixtures are made up, nothing is downloaded or ingested, and no crest or logo is
involved. A test therefore cannot depend on data the ingestion produces, and it
cannot introduce trademarked content or a real club name into the suite.

The seasons default to 2098-99 or later, a range no data source covers, so a
synthetic row stays clear of the seeded reference seasons and the ingested
matches. A test can then assert on its own rows without the shared database
changing the answer.

Every helper inserts through the ``Session`` it is given and flushes, never
commits: the caller's transaction is the only thing that decides whether the
row survives, and the flush is what fills the generated identifier the test
needs to assert on.
"""

import datetime

from lmi_core.db.models import Match, Season, Team
from sqlalchemy.orm import Session


def create_season(
    session: Session,
    *,
    code: str = "2098-99",
    start_year: int = 2098,
    fd_code: str | None = None,
) -> Season:
    """Insert one season and return the ORM row with its generated id.

    ``fd_code`` is the data-source code of the season, a four-digit pair such as
    ``9899``. It defaults to the one derived from ``start_year``, and is a
    parameter so a test can also build a row whose source code is unrelated to
    the season code.
    """
    row = Season(
        code=code,
        start_year=start_year,
        fd_code=fd_code if fd_code is not None else _fd_code(start_year),
    )
    session.add(row)
    session.flush()
    return row


def create_team(session: Session, *, name: str = "Test FC Alpha") -> Team:
    """Insert one team and return the ORM row with its generated id."""
    row = Team(name=name)
    session.add(row)
    session.flush()
    return row


def create_match(
    session: Session,
    *,
    season: Season,
    home: Team,
    away: Team,
    kickoff_date: datetime.date = datetime.date(2098, 8, 15),
    matchday: int = 1,
    status: str = "scheduled",
    home_goals: int | None = None,
    away_goals: int | None = None,
) -> Match:
    """Insert one fixture and return the ORM row with its generated id.

    The goals are passed as an argument only for a played match, whose result is
    then derived from the score so the row satisfies the check constraints that
    tie ``result`` to the goals and require a played row to carry both. A
    scheduled match is written with no goals and no result, as the schema
    requires: NULL there means "not known yet", not "no goals".

    Raises:
        ValueError: the match is played and one of the goal counts is missing,
            because a played row without a complete score cannot be stored.
    """
    if status == "played":
        result = _result(home_goals, away_goals)
    else:
        home_goals = None
        away_goals = None
        result = None

    row = Match(
        season_id=season.id,
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_date=kickoff_date,
        matchday=matchday,
        status=status,
        home_goals=home_goals,
        away_goals=away_goals,
        result=result,
    )
    session.add(row)
    session.flush()
    return row


def _fd_code(start_year: int) -> str:
    """Return the data-source season code of a season starting in ``start_year``.

    The code is the last two digits of the start year followed by the last two
    digits of the year it ends in, so a season starting in 2098 is ``9899``.
    """
    return str(start_year)[2:] + str(start_year + 1)[2:]


def _result(home_goals: int | None, away_goals: int | None) -> str:
    """Return the 1X2 result of a complete score.

    Raises:
        ValueError: one of the goal counts is missing, so no result can be
            derived and a played row would violate the schema.
    """
    if home_goals is None or away_goals is None:
        raise ValueError("a played match needs both goal counts to derive its result")
    if home_goals > away_goals:
        return "H"
    if home_goals == away_goals:
        return "D"
    return "A"
