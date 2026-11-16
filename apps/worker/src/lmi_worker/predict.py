"""The ``predict`` worker command: store today's live predictions, or report them.

Two paths share one command. Without ``--report`` the command opens one
prediction run for the Madrid date it starts on, builds that date's batch with
:func:`lmi_core.live.predict.build_predictions` and stores it with
:func:`lmi_core.live.store.write_batch`. With ``--report`` it reads the stored
predictions back and prints the live metrics document the API serves, so a
reader can inspect the scores without writing a row. Nothing here computes a
probability, a count or a metric: the builder, the store and the scorer own
those rules, and the command only decides which of them to call and what to
print.

The as-of date is always :func:`lmi_core.clock.madrid_today`, and there is
deliberately no flag for it: the insert trigger accepts only the Madrid date the
row is created on, so a date a caller passed could never be stored.

That date-based guard has one consequence, worth stating where the schedule is
decided: a match dated ``D`` is last predicted on ``D-1``, because the batch of
day ``D`` covers only the fixtures strictly after ``D``. The daily 06:00 Madrid
cycle is therefore what keeps the last forecast before kickoff; a skipped day
leaves the matches of that day with the previous day's forecast and none from
the day itself.

The run is opened before the batch is built, so every later failure is recorded
on the run instead of vanishing. The per-model ``inserted`` and ``existing``
counts of the summary come from the run's own rows, because every row this run
inserted carries its ``run_id`` while a row that already existed belongs to an
earlier run.

The module-level names the command calls are looked up at call time on purpose
(``make_engine``, ``session_factory``, ``build_predictions`` and the store
functions), which is what lets the tests replace the database and the builder
without a live server.
"""

import argparse
import datetime
import logging
import sys
import uuid
from typing import Final

from lmi_core.clock import madrid_today
from lmi_core.config import get_settings
from lmi_core.db.models import Prediction, PredictionRun
from lmi_core.db.session import make_engine, session_factory
from lmi_core.live.data import load_prediction_rows
from lmi_core.live.predict import (
    DEFAULT_HORIZON_DAYS,
    MAX_HORIZON_DAYS,
    MIN_HORIZON_DAYS,
    PredictionBatch,
    build_predictions,
)
from lmi_core.live.scoring import evaluate, live_metrics
from lmi_core.live.store import fail_run, start_run, write_batch
from lmi_core.modeling.data import load_match_data
from lmi_core.modeling.registry import MODEL_NAMES
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH: Final = 1000
"""How many characters of an error message one run row or line may carry."""

_RUNNING: Final = "running"
"""The only run state from which a run can still be failed."""


def add_predict_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add the ``predict`` subparser to the worker command line."""
    parser = subparsers.add_parser(
        "predict",
        help="Store the live predictions of the Madrid date today, or report them.",
    )
    parser.add_argument(
        "--horizon-days",
        type=_horizon_days,
        default=DEFAULT_HORIZON_DAYS,
        help=(
            f"how many days after the as-of date the batch reaches, "
            f"{MIN_HORIZON_DAYS} to {MAX_HORIZON_DAYS}"
        ),
    )
    parser.add_argument(
        "--models",
        metavar="NAMES",
        default=MODEL_NAMES,
        type=_model_names,
        help="comma-separated model names; defaults to every registered model",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="print the live metrics report instead of storing a batch",
    )


def run_predict_command(args: argparse.Namespace) -> int:
    """Run the prediction the parsed arguments describe and return the exit code.

    Returns:
        0 when the batch was stored or the report was printed, 1 when the
        database cannot be reached or the batch failed and was recorded as
        failed, 2 when the database configuration is incomplete.
    """
    as_of = madrid_today()
    try:
        engine = make_engine(get_settings())
    except ValueError as error:
        # The message names the missing environment variables and never a value.
        print(f"predict: {error}", file=sys.stderr)
        return 2

    try:
        with session_factory(engine)() as session:
            if args.report:
                return _report(session, as_of)
            return _store(args, session, as_of)
    except SQLAlchemyError:
        # The driver text can carry the connection URL, so only the failure kind
        # is printed.
        print("predict: cannot reach the database", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


def _horizon_days(value: str) -> int:
    """Parse ``--horizon-days`` and refuse a value the column cannot store.

    Raises:
        argparse.ArgumentTypeError: the value is not an integer, or it falls
            outside ``1..60``, the range of the column's own CHECK constraint.
            ``argparse`` turns the error into exit code 2 before any database
            work.
    """
    try:
        days = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--horizon-days needs a whole number of days, got {value!r}"
        ) from None
    if not MIN_HORIZON_DAYS <= days <= MAX_HORIZON_DAYS:
        raise argparse.ArgumentTypeError(
            f"--horizon-days must be between {MIN_HORIZON_DAYS} and {MAX_HORIZON_DAYS}, got {days}"
        )
    return days


def _model_names(value: str) -> tuple[str, ...]:
    """Split and validate a comma-separated model list.

    Raises:
        argparse.ArgumentTypeError: the list is empty, names a model that is not
            registered, or names one model twice.
    """
    names = tuple(part.strip() for part in value.split(",") if part.strip())
    known = ", ".join(MODEL_NAMES)
    if not names:
        raise argparse.ArgumentTypeError(
            f"--models needs at least one name; known models are {known}"
        )
    unknown = [name for name in names if name not in MODEL_NAMES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown model {', '.join(unknown)}; known models are {known}"
        )
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise argparse.ArgumentTypeError(
            f"model {', '.join(repeated)} given twice; known models are {known}"
        )
    return names


def _store(args: argparse.Namespace, session: Session, as_of: datetime.date) -> int:
    """Build, store and summarise one batch inside one session.

    The run is opened first, so a failure of anything between it and the stored
    rows leaves a record instead of a silent gap.
    """
    run_id = start_run(
        session,
        as_of=as_of,
        horizon_days=args.horizon_days,
        trigger="cli",
    )
    logger.info("run %s", run_id)
    try:
        batch = build_predictions(
            session,
            as_of=as_of,
            horizon_days=args.horizon_days,
            models=args.models,
        )
        result = write_batch(session, run_id, batch)
        inserted = _inserted_per_model(session, run_id)
    except Exception as error:
        # Everything after start_run sits under this guard: a run left running
        # by an escaping exception would never be transitioned, because nothing
        # else in the system does it.
        text = _error_text(error)
        _mark_failed(session, run_id, error=text)
        print(f"predict: {text}", file=sys.stderr)
        return 1
    except BaseException as error:
        # An interruption is not an Exception, so it would skip the guard above
        # and leave the committed run ``running`` for ever. Record it, then let
        # the original exception out unchanged: the process still exits non-zero
        # and the traceback keeps its frames.
        _mark_failed(session, run_id, error=_error_text(error))
        raise

    _print_counts(batch, inserted)
    print(
        f"predict: run {run_id} as_of={batch.as_of.isoformat()} "
        f"horizon={batch.horizon_days} history={batch.n_history} "
        f"fixtures={batch.n_fixtures} inserted={result.n_inserted} "
        f"existing={result.n_existing} no_forecast={result.n_no_forecast}"
    )
    return 0


def _report(session: Session, as_of: datetime.date) -> int:
    """Print the live metrics report of the stored predictions."""
    rows = load_prediction_rows(session)
    data = load_match_data(session)
    odds_by_match = {
        int(match_id): odds for match_id, odds in zip(data.match_id, data.odds, strict=True)
    }
    response = live_metrics(evaluate(rows), odds_by_match, today=as_of)
    print(response.model_dump_json(indent=2))
    return 0


def _inserted_per_model(session: Session, run_id: uuid.UUID) -> dict[str, int]:
    """Return how many rows this run inserted per model.

    Every row the run inserted carries its ``run_id``, while a row that already
    existed keeps the id of the earlier run that wrote it, so this count is
    exactly the inserted part of the batch.
    """
    statement = (
        select(Prediction.model, func.count())
        .where(Prediction.run_id == run_id)
        .group_by(Prediction.model)
    )
    return {str(model): int(count) for model, count in session.execute(statement)}


def _print_counts(batch: PredictionBatch, inserted: dict[str, int]) -> None:
    """Print one line per requested model, in the order the batch built them.

    The inserted count is the run's own; the existing count is the rest of the
    model's rows in the batch, which an earlier run already stored.
    """
    for counts in batch.counts:
        n_inserted = inserted.get(counts.model, 0)
        print(
            f"predict: {counts.model} rows={counts.n_rows} inserted={n_inserted} "
            f"existing={counts.n_rows - n_inserted} no_forecast={counts.n_no_forecast}"
        )


def _mark_failed(session: Session, run_id: uuid.UUID, *, error: str) -> None:
    """Record the failure of ``run_id`` while it is still running, best effort.

    The transaction behind the failed step may have aborted the session, and an
    aborted transaction rejects every later statement, ``fail_run``'s own lock
    query included, so the session is rolled back first. A bookkeeping failure
    here never masks the caller's original error, which the caller still
    reports.
    """
    try:
        session.rollback()
        status = session.scalar(select(PredictionRun.status).where(PredictionRun.id == run_id))
        if status == _RUNNING:
            fail_run(session, run_id, error=error)
    except ValueError, SQLAlchemyError:
        # The database may be gone; the caller still reports the original error.
        logger.info("could not mark run %s as failed", run_id)


def _error_text(error: BaseException) -> str:
    """Return the diagnostic of ``error`` that may be stored and printed.

    An :class:`~sqlalchemy.exc.IntegrityError` keeps its message: the live
    guards answer with a ``lmi: ...`` text that says which rule refused the row,
    which is the useful part. Any other
    :class:`~sqlalchemy.exc.SQLAlchemyError` is reduced to the exception class
    name, because a driver message can carry the connection URL. Every other
    exception keeps ``class name: message``. Both kept forms are truncated to
    :data:`MAX_ERROR_LENGTH` characters.
    """
    if isinstance(error, IntegrityError):
        return _truncate(f"{type(error).__name__}: {error}")
    if isinstance(error, SQLAlchemyError):
        return type(error).__name__
    return _truncate(f"{type(error).__name__}: {error}")


def _truncate(text: str) -> str:
    """Cut ``text`` to the longest diagnostic a run row may carry."""
    return text[:MAX_ERROR_LENGTH]
