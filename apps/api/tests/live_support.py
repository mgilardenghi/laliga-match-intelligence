"""Synthetic stored predictions for the live read tests.

Everything inserted here is invented: the club names, the season codes, the
probabilities and the matches are made up, nothing is downloaded or ingested,
and no crest or logo is involved. A test therefore cannot depend on data a real
source produced, and it cannot introduce trademarked content or a real club name
into the suite.

A stored prediction cannot be inserted like any other row. The
``predictions_guard_insert`` trigger stamps ``created_at`` with ``now()``,
refuses a match that is no longer ``scheduled``, refuses an ``as_of_date`` that
is not the Madrid creation date and refuses a row stamped at or after Madrid
midnight of the match day; the ``predictions_immutable`` trigger then refuses
every update and delete. The helpers here therefore recover those rules instead
of restating them: a fixture is dated after Madrid today, the "today" comes from
the database and never from the Python clock, and a match is moved to another
state only after its prediction has been stored.

The one exception is the multi-day ledger, which no insert can build: the guard
accepts only today's as-of date, and the immutability triggers forbid rewriting
a row afterwards. :func:`create_ledger_prediction` therefore disables the insert
guard around its own insert. Both the DDL and the row live in the caller's
transaction, so the trigger is back in force before the test ends and the
rollback of the fixture undoes the whole thing.

Every helper flushes and never commits: the caller's transaction is the only
thing that decides whether a row survives, and the flush is what fills the
generated identifier the test asserts on.
"""

import datetime
from typing import cast

from lmi_core.db.models import Match, Prediction, PredictionRun
from sqlalchemy import func, text, update
from sqlalchemy.orm import Session

_GUARD_INSERT_DISABLE = "ALTER TABLE predictions DISABLE TRIGGER predictions_guard_insert"
"""Turn the insert guard off for the rest of the caller's transaction."""

_GUARD_INSERT_ENABLE = "ALTER TABLE predictions ENABLE TRIGGER predictions_guard_insert"
"""Turn the insert guard back on, so a later insert in the test is guarded again."""


def madrid_today(session: Session) -> datetime.date:
    """Return the Madrid date the database reports for ``now()``.

    The value comes from PostgreSQL, never from the Python clock. The insert
    guard compares ``as_of_date`` with the database's own Madrid date, so a
    fixture built from the host clock could produce a row the trigger rejects
    near midnight, and the test would fail for the wrong reason.
    """
    today = session.scalar(text("SELECT (now() AT TIME ZONE 'Europe/Madrid')::date"))
    return cast(datetime.date, today)


def create_prediction_run(
    session: Session,
    *,
    as_of: datetime.date | None = None,
    horizon_days: int = 14,
    trigger: str = "cli",
    code_version: str = "test",
) -> PredictionRun:
    """Insert one prediction run and return it, still ``running``.

    A run stays ``running`` on purpose: the guard of the table allows only the
    transition out of ``running``, and none of the read tests needs a finished
    run, which would only pin a timestamp nothing serves.
    """
    row = PredictionRun(
        as_of_date=as_of if as_of is not None else madrid_today(session),
        status="running",
        trigger=trigger,
        horizon_days=horizon_days,
        code_version=code_version,
    )
    session.add(row)
    session.flush()
    return row


def create_prediction(
    session: Session,
    *,
    match: Match,
    model: str = "home_win",
    as_of: datetime.date | None = None,
    probabilities: tuple[float, float, float] = (0.5, 0.3, 0.2),
    model_version: str = "1.0",
    model_config: dict[str, object] | None = None,
    odds_source: str | None = None,
    run: PredictionRun | None = None,
) -> Prediction:
    """Insert one stored prediction and return the ORM row with its identifier.

    The match must be ``scheduled`` and dated after Madrid today. Both are rules
    of the ``predictions_guard_insert`` trigger rather than of this helper: it
    refuses a match that is no longer scheduled, an ``as_of_date`` that is not
    the Madrid creation date and a row stamped at or after Madrid midnight of
    the match day, so a match dated today cannot carry a prediction at all.

    ``as_of`` defaults to Madrid today, the only date the guard accepts. A
    caller that passes an earlier date has to use
    :func:`create_ledger_prediction`, which is what the ledger exception is for.

    ``probabilities`` is the ``(home, draw, away)`` triple, which the database
    checks to sum to one, and ``model_config`` defaults to an empty object
    because the column is a non-null JSON object.
    """
    return _insert(
        session,
        match=match,
        model=model,
        as_of=as_of if as_of is not None else madrid_today(session),
        probabilities=probabilities,
        model_version=model_version,
        model_config=model_config,
        odds_source=odds_source,
        run=run,
    )


def create_ledger_prediction(
    session: Session,
    *,
    match: Match,
    model: str = "home_win",
    as_of: datetime.date | None = None,
    probabilities: tuple[float, float, float] = (0.5, 0.3, 0.2),
    model_version: str = "1.0",
    model_config: dict[str, object] | None = None,
    odds_source: str | None = None,
    run: PredictionRun | None = None,
) -> Prediction:
    """Insert one stored prediction dated earlier than Madrid today.

    A multi-day ledger is the only way to test what an older as-of date earns,
    and no insert can build one: the guard accepts only the Madrid date of
    creation, and the immutability triggers forbid rewriting a row afterwards.
    This helper therefore disables the insert guard around its own insert and
    enables it again immediately after.

    The DDL is transactional like the row, so nothing leaks: the trigger is
    guarded again for any later insert of the same test, and the fixture's
    rollback undoes both the row and the statements that allowed it.

    ``as_of`` defaults to yesterday, the shallowest ledger step, because the
    rows of one group must differ in their as-of date to be distinguishable.
    """
    session.execute(text(_GUARD_INSERT_DISABLE))
    row = _insert(
        session,
        match=match,
        model=model,
        as_of=as_of if as_of is not None else madrid_today(session) - datetime.timedelta(days=1),
        probabilities=probabilities,
        model_version=model_version,
        model_config=model_config,
        odds_source=odds_source,
        run=run,
    )
    session.execute(text(_GUARD_INSERT_ENABLE))
    return row


def mark_played(session: Session, match: Match, *, home_goals: int, away_goals: int) -> None:
    """Move a stored fixture to ``played`` with a complete score.

    ``result`` is derived from the score, ``withdrawn_at`` is cleared and
    ``updated_at`` is restamped, so the row satisfies the same checks the loader
    satisfies. A played match must carry both goals and a result that agrees
    with them, and it must not carry a withdrawal stamp.
    """
    session.execute(
        update(Match)
        .where(Match.id == match.id)
        .values(
            status="played",
            home_goals=home_goals,
            away_goals=away_goals,
            result=_result(home_goals, away_goals),
            withdrawn_at=None,
            updated_at=func.now(),
        )
    )
    session.flush()


def mark_withdrawn(session: Session, match: Match) -> None:
    """Move a stored fixture to ``withdrawn`` and keep its stored predictions.

    The score, the half-time score and the result are cleared because a row that
    is not played must carry none of them, and ``withdrawn_at`` is stamped
    because the schema ties the stamp to the status in both directions. The
    predictions of the match stay: that is the reason a withdrawn fixture is
    kept instead of deleted.
    """
    session.execute(
        update(Match)
        .where(Match.id == match.id)
        .values(
            status="withdrawn",
            home_goals=None,
            away_goals=None,
            ht_home_goals=None,
            ht_away_goals=None,
            result=None,
            withdrawn_at=func.now(),
            updated_at=func.now(),
        )
    )
    session.flush()


def move_match(session: Session, match: Match, *, kickoff_date: datetime.date) -> None:
    """Move a stored fixture to another date.

    Moving the date is what makes a stored prediction late: the evaluation
    compares the write instant of the row with Madrid midnight of the match's
    *current* date, so a fixture moved to a day that already opened makes every
    stored row of it late, however well timed it was.
    """
    session.execute(
        update(Match)
        .where(Match.id == match.id)
        .values(kickoff_date=kickoff_date, updated_at=func.now())
    )
    session.flush()


def _insert(
    session: Session,
    *,
    match: Match,
    model: str,
    as_of: datetime.date,
    probabilities: tuple[float, float, float],
    model_version: str,
    model_config: dict[str, object] | None,
    odds_source: str | None,
    run: PredictionRun | None,
) -> Prediction:
    """Insert one stored prediction with the caller's values and flush it.

    ``created_at`` is left to the database: the insert guard overwrites it with
    ``now()`` in every case, so a value written here would be discarded.
    """
    row = Prediction(
        run_id=(run if run is not None else create_prediction_run(session, as_of=as_of)).id,
        match_id=match.id,
        model=model,
        model_version=model_version,
        model_config=model_config if model_config is not None else {},
        as_of_date=as_of,
        p_home=probabilities[0],
        p_draw=probabilities[1],
        p_away=probabilities[2],
        odds_source=odds_source,
    )
    session.add(row)
    session.flush()
    return row


def _result(home_goals: int, away_goals: int) -> str:
    """Return the 1X2 result of a complete score, from the home side's point of view."""
    if home_goals > away_goals:
        return "H"
    if home_goals == away_goals:
        return "D"
    return "A"
