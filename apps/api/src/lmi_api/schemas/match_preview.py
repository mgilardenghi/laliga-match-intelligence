"""Match-preview payloads: the pre-kickoff view of one fixture.

The resource serves three stored things and derives the rest. The fixture, the
played matches behind the two form summaries and the head-to-head record, the
stored predictions of the fixture and its pre-closing 1x2 price are rows another
service already wrote. The letters, points and goals of a form summary, the
three head-to-head counters, the ``evaluation`` of each prediction and the
margin-removed ``implied`` probabilities are derived on read, and no rule of any
of them is restated here: they come from
:func:`lmi_api.views.form_summary`, :func:`lmi_api.views.head_to_head_records`,
:func:`lmi_core.live.scoring.evaluate` and
:func:`lmi_core.modeling.market.remove_margin` respectively.

The resource obeys two leakage rules, and the shapes below carry their
consequence. ``FormSummary.matches`` and ``HeadToHead.matches`` hold only
matches whose kickoff date is strictly earlier than the fixture's own, and
:class:`MatchOdds` is the pre-closing price of the fixture, never a closing one.
A fixture that has since been played therefore serves its preview, which is what
a reader could have seen before kickoff.

Two nulls carry meaning and neither means "zero". ``odds`` is null when the
fixture carries no eligible price, which is the normal state of a match still to
be played and is not a price of zero. ``predictions`` is empty when no model has
stored a forecast for the fixture yet, which is why the child
:class:`~lmi_api.schemas.predictions.PredictionBrief` is never padded with a
made-up row.
"""

from typing import Literal

from pydantic import BaseModel

from lmi_api.schemas.matches import MatchSummary
from lmi_api.schemas.predictions import PredictionBrief, Probabilities
from lmi_api.schemas.team_focus import FormSummary

__all__ = ["HeadToHead", "MatchOdds", "MatchPreview"]


class HeadToHead(BaseModel):
    """The prior meetings of the two clubs of the fixture, and their record.

    ``played`` counts every prior meeting of the pair, at either venue, dated
    strictly before the fixture's own kickoff date. ``matches`` lists only the
    ten most recent of those meetings, newest first, because a league pair meets
    twice a season and a long rivalry would otherwise dominate the payload. The
    two fields are therefore not the same length and ``played`` may exceed
    ``len(matches)``: a client that renders the record reads the counters, and a
    client that renders the recent meetings reads the list.

    The three counters are seen from *this fixture's* home side, not from the
    venue of each earlier meeting: :attr:`home_side_wins` counts the meetings
    the home team of this fixture won, :attr:`away_side_wins` the ones its
    opponent won, and :attr:`draws` the ones neither won, whatever end of the
    ground each club played at. A venue-based count would describe the home
    teams of the earlier meetings instead, which is a different club in half of
    them.
    """

    played: int
    home_side_wins: int
    draws: int
    away_side_wins: int
    matches: list[MatchSummary]


class MatchOdds(BaseModel):
    """The pre-closing 1x2 price of the fixture and the probabilities it implies.

    ``bookmaker`` is the source of the row, selected by the loader's bookmaker
    priority, and ``is_closing`` is literally false: a closing price is only
    known at kickoff, so serving one would describe the fixture with information
    nobody could have had earlier. The three prices are the stored decimals as
    floats, in ``home``/``draw``/``away`` order.

    ``implied`` is derived from exactly these three prices by removing the
    bookmaker's margin proportionally, so its values sum to one and are not
    stored anywhere. It is a forecast of the market, not of a model, which is
    why it is a separate object from ``predictions``.
    """

    bookmaker: str
    is_closing: Literal[False]
    home: float
    draw: float
    away: float
    implied: Probabilities


class MatchPreview(BaseModel):
    """Everything the preview of one fixture serves.

    ``match`` is the fixture itself, nested rather than referenced, so a client
    renders the clubs, the date and the status from one response. ``home_form``
    and ``away_form`` are the recent form of both sides, read strictly before
    the fixture's kickoff date. ``head_to_head`` is the record of the pair and
    its ten most recent meetings. ``predictions`` holds the standing stored
    forecast of every model of the fixture, whatever its evaluation. ``odds`` is
    the pre-closing price of the fixture or null when it carries none.

    The spec §6 items this resource deliberately does not carry are M5: the
    opponent's profile beyond the form of both sides, and how much the result
    moves the relegation probability. Both need an input or a core rule this
    resource does not own, not another field here.
    """

    match: MatchSummary
    home_form: FormSummary
    away_form: FormSummary
    head_to_head: HeadToHead
    predictions: list[PredictionBrief]
    odds: MatchOdds | None
