"""Match preview endpoint: the pre-kickoff view of one fixture.

The resource answers the question a dashboard asks before a match: how are the
two clubs doing, how have they met before, what do the models say and what does
the market say. It is a read over rows other services already own. The fixture
and the played results behind the form and the head-to-head are stored, the
forecasts of the fixture are stored predictions and the price is a stored odds
row.

No rule of its own is implemented here. The form and the head-to-head record
come from the shared readers of :mod:`lmi_api.views`, the ``evaluation`` of every
prediction from :func:`lmi_core.live.scoring.evaluate`, which is the only place
that decides whether a stored forecast was on time, and the implied
probabilities from :func:`lmi_core.modeling.market.remove_margin`, which is the
only margin removal in the repository. This module only decides which rows the
preview reads and how it composes them.

The resource obeys two leakage rules, and both are consequences of the rows it
selects rather than of a filter here:

* the two form summaries and the head-to-head are read strictly before the
  fixture's own kickoff date, so a match that has since been played serves its
  pre-match view and never its own result or a later meeting;
* the price served is the *pre-closing* one. A closing price is only known at
  kickoff, so it is never read, whatever a caller stored.

The endpoint is served on the ``/matches`` prefix and under the ``matches`` tag
of the M2 match resource, so the documentation groups the preview with the
fixture it describes and a generated client sees one resource group per entity
rather than one per path shape. The route only nests deeper; ``matches.py`` is
not edited.
"""

import datetime
from typing import Final

import numpy as np
from fastapi import APIRouter, HTTPException
from lmi_core.db.models import MatchOdd as MatchOddModel
from lmi_core.live.data import ODDS_PRIORITY
from lmi_core.live.scoring import evaluate
from lmi_core.modeling.market import remove_margin
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from lmi_api import views
from lmi_api.db import SessionDep
from lmi_api.errors import PROBLEM_RESPONSES
from lmi_api.schemas.match_preview import HeadToHead, MatchOdds, MatchPreview
from lmi_api.schemas.matches import MatchSummary
from lmi_api.schemas.predictions import Probabilities
from lmi_api.schemas.team_focus import FormSummary
from lmi_api.schemas.teams import Team

router = APIRouter(prefix="/matches", tags=["matches"])

_FORM_LENGTH: Final[int] = 5
"""How many played matches each form summary covers: the five most recent."""

_HEAD_TO_HEAD_LENGTH: Final[int] = 10
"""How many prior meetings the payload lists, however many the pair played."""

_ONE_X_TWO: Final[str] = "1x2"
"""The market of a 1x2 price, the only market the loader prices a match with."""


@router.get("/{match_id}/preview", responses=PROBLEM_RESPONSES)
def get_match_preview(match_id: int, session: SessionDep) -> MatchPreview:
    """Return the pre-kickoff view of one fixture.

    ``match`` is the fixture itself, read through the shared match reader so the
    payload names the clubs, the season and the date the same way every other
    match resource does.

    ``home_form`` and ``away_form`` are the form of each side over the played
    matches dated strictly before the fixture's own kickoff date, five at most
    and across seasons. The bound is the leakage rule: the fixture itself, a
    fixture of the same day and every later one are excluded, so the form is the
    one a reader could have seen before kickoff, and a played match's preview
    shows the form it had then rather than the form it has now.

    ``head_to_head`` is the same rule applied to the pair. A league pair meets
    twice a season, so the full prior set is read and counted -- truncating the
    read would silently change ``played`` -- and only the payload list is capped
    at the ten most recent meetings. The three counters are seen from *this*
    fixture's home team, not from the venue of each earlier meeting, so the club
    that was the visitor in a prior meeting still counts its own wins.

    ``predictions`` holds the standing stored forecast of every model of the
    fixture, whatever it earned: a played match yields ``scored`` briefs, a
    scheduled one ``pending``, a withdrawn one ``withdrawn`` and a fixture whose
    date moved earlier than its rows ``late``. The evaluation is the core
    scorer's, so this resource cannot disagree with ``/v1/predictions`` or
    ``/v1/model-metrics/live``.

    ``odds`` is the pre-closing 1x2 price of the fixture, selected by the
    bookmaker priority the live loader uses, and null when the fixture carries
    no eligible price -- the normal state of a match still to be played, since
    the current sources publish odds only with results. No price is invented and
    no closing price is served.

    The spec §6 items this resource deliberately does not carry are M5: the
    opponent's profile beyond the form of both sides, and how much the result
    moves the relegation probability. Both need an input or a core rule this
    task does not own, not another field here.

    The request issues at most nine statements whatever the size of the payload:
    one for the fixture, two for each of the two form summaries, two for the
    head-to-head read, one for the stored predictions and one for the price. A
    read that matched nothing issues fewer, never more.

    The return annotation is the response model, so no ``response_model`` is
    passed.

    Raises:
        HTTPException: 404 when no stored match has ``match_id``. The detail is
            a fixed sentence and never repeats the identifier.
    """
    match = views.load_match_summaries(session, [match_id]).get(match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    stored = views.load_stored_predictions(session, match_ids=[match_id])
    prediction_ids = {entry.identity: entry.id for entry in stored}

    return MatchPreview(
        match=match,
        home_form=_form(session, match.home_team, before=match.kickoff_date),
        away_form=_form(session, match.away_team, before=match.kickoff_date),
        head_to_head=_head_to_head(session, match),
        predictions=views.prediction_briefs(
            evaluate([entry.row for entry in stored]), prediction_ids=prediction_ids
        ),
        odds=_pre_closing_odds(session, match_id),
    )


def _form(session: Session, team: Team, *, before: datetime.date) -> FormSummary:
    """Return the form of ``team`` over the matches dated strictly before ``before``.

    The scope is the last :data:`_FORM_LENGTH` played matches of the team, home
    or away and in any season, which is the list the team focus serves as well:
    the two resources describe a form with the same reader, so a reader cannot
    read one string on the focus page and another on the preview.
    """
    return views.form_summary(
        team,
        views.load_played_summaries(session, team_id=team.id, before=before, limit=_FORM_LENGTH),
    )


def _head_to_head(session: Session, match: MatchSummary) -> HeadToHead:
    """Return the prior meetings of the fixture's pair and the record they add up to.

    Every prior meeting is read, because ``played`` counts all of them, and the
    payload list is then capped at :data:`_HEAD_TO_HEAD_LENGTH`: the read is not
    truncated, so the counters cover the whole history of the pair.
    """
    meetings = views.load_head_to_head(
        session,
        home_team_id=match.home_team.id,
        away_team_id=match.away_team.id,
        before=match.kickoff_date,
    )
    home_side_wins, draws, away_side_wins = views.head_to_head_records(
        meetings, home_team_id=match.home_team.id
    )
    return HeadToHead(
        played=len(meetings),
        home_side_wins=home_side_wins,
        draws=draws,
        away_side_wins=away_side_wins,
        matches=meetings[:_HEAD_TO_HEAD_LENGTH],
    )


def _pre_closing_odds(session: Session, match_id: int) -> MatchOdds | None:
    """Return the pre-closing 1x2 price of one fixture, or ``None`` when it has none.

    The eligible rows are the ``1x2`` prices with ``is_closing`` false from a
    bookmaker of :data:`lmi_core.live.data.ODDS_PRIORITY`, and the selected one
    is the most trusted of them. That order is read from the priority constant
    and never restated here: the constant's own docstring says it must stay
    equal to the order the modeling loader applies, and
    :func:`lmi_core.live.data.load_upcoming` prices a fixture with the same
    rows, so a preview and a forecast cannot report different bookmakers for one
    match. The read is capped to one row, so a fixture quoted by three bookmakers
    costs the same as one quoted by one.

    A closing price is never eligible, whatever its bookmaker: it is only known
    at kickoff, and the design of every odds read in this codebase drops it.

    ``implied`` is :func:`lmi_core.modeling.market.remove_margin` over the three
    prices of this one row as a ``(1, 3)`` array, the order the function reads.
    The row carries real decimal prices because the columns are non-null and
    guarded above one, so the result is never an all-NaN row.

    Args:
        session: a session bound to the application database.
        match_id: the fixture whose price is requested.

    Returns:
        The price with its implied probabilities, or ``None`` when the fixture
        carries no eligible price.
    """
    priority = case(
        *((MatchOddModel.bookmaker == name, rank) for rank, name in enumerate(ODDS_PRIORITY)),
        else_=len(ODDS_PRIORITY),
    )
    row = session.execute(
        select(
            MatchOddModel.bookmaker,
            MatchOddModel.home,
            MatchOddModel.draw,
            MatchOddModel.away,
        )
        .where(
            MatchOddModel.match_id == match_id,
            MatchOddModel.is_closing.is_(False),
            MatchOddModel.market == _ONE_X_TWO,
            MatchOddModel.bookmaker.in_(ODDS_PRIORITY),
        )
        .order_by(priority)
        .limit(1)
    ).first()
    if row is None:
        return None

    prices = np.array([[float(row.home), float(row.draw), float(row.away)]], dtype=np.float64)
    implied = remove_margin(prices)[0]
    return MatchOdds(
        bookmaker=str(row.bookmaker),
        is_closing=False,
        home=float(row.home),
        draw=float(row.draw),
        away=float(row.away),
        implied=Probabilities(
            home=float(implied[0]),
            draw=float(implied[1]),
            away=float(implied[2]),
        ),
    )
