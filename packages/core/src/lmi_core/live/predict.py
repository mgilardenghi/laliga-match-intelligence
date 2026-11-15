"""Produce the live prediction rows of one as-of date.

The batch is built from two inputs only: played matches strictly before the
as-of date, and scheduled matches strictly after it up to the horizon. The
history is loaded once and every requested model is fitted on that same
snapshot, so two models of one batch can never see different data. The
``as_of`` date is a parameter, never read from the clock, so the function is
testable on any date and production passes
:func:`lmi_core.clock.madrid_today`.

This module is the produce half of the live path. It writes nothing: the store
block persists the batch, and the stored rows are immutable afterwards.
"""

import datetime
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, cast

import numpy as np
from sqlalchemy.orm import Session

from lmi_core.evaluation.backtest import data_fingerprint
from lmi_core.live.data import load_history, load_upcoming
from lmi_core.modeling.registry import MODEL_NAMES, make_model
from lmi_core.modeling.types import LeakageError

DEFAULT_HORIZON_DAYS: Final = 14
"""How many days after the as-of date one default batch covers."""

MIN_HORIZON_DAYS: Final = 1
MAX_HORIZON_DAYS: Final = 60


@dataclass(frozen=True)
class PredictedRow:
    """One probability triple ready to be stored.

    The identity columns repeat per row so the store can insert without
    consulting the model again, and ``odds_source`` records which bookmaker
    priced the fixture, ``None`` when no eligible price existed.
    """

    match_id: int
    model: str
    model_version: str
    model_config: dict[str, object]
    p_home: float
    p_draw: float
    p_away: float
    odds_source: str | None


@dataclass(frozen=True)
class ModelCounts:
    """How one model covered the batch.

    ``n_no_forecast`` is the number of fixtures the model deliberately left
    without a row, such as the market model on a fixture without odds. A model
    that forecast nothing still keeps its entry with ``n_rows = 0``.
    """

    model: str
    n_fixtures: int
    n_rows: int
    n_no_forecast: int


@dataclass(frozen=True)
class PredictionBatch:
    """Everything one as-of date produces, before anything is stored.

    ``data_fingerprint`` identifies the history the fit saw, and the counts let
    the run row record coverage without recomputing it from the rows.
    """

    as_of: datetime.date
    horizon_days: int
    n_history: int
    n_fixtures: int
    data_fingerprint: str
    rows: tuple[PredictedRow, ...]
    counts: tuple[ModelCounts, ...]


def build_predictions(
    session: Session,
    *,
    as_of: datetime.date,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    models: Sequence[str] = MODEL_NAMES,
    seed: int = 0,
) -> PredictionBatch:
    """Build the prediction rows for ``as_of`` from the stored matches.

    History is every played match dated strictly before ``as_of`` and fixtures
    are the scheduled matches with ``as_of < kickoff_date <= as_of +
    horizon_days``. Both are loaded once for the whole batch. The leakage guard
    runs before any model can produce a number, and it checks each side only
    when that side is non-empty: an empty history always passes, and an empty
    fixture set always passes, while the other side is still checked.

    Args:
        session: a read-only session bound to the application database.
        as_of: the Madrid date the predictions are for. The clock is never read.
        horizon_days: how many days past ``as_of`` the fixtures may lie.
        models: the model names to run, in report order.
        seed: the seed passed to every model.

    Returns:
        The batch, with one :class:`ModelCounts` per requested model in the
        requested order and the rows of every model that forecast a fixture.

    Raises:
        ValueError: ``horizon_days`` is outside ``1..60``, or a requested model
            name is not registered.
        LeakageError: the history contains a match on or after ``as_of``, or a
            fixture is dated on or before it.
    """
    if not MIN_HORIZON_DAYS <= horizon_days <= MAX_HORIZON_DAYS:
        raise ValueError(
            f"horizon_days must be between {MIN_HORIZON_DAYS} and {MAX_HORIZON_DAYS}, "
            f"got {horizon_days}"
        )

    as_of_day = np.datetime64(as_of, "D")
    history = load_history(session, as_of)
    fixtures = load_upcoming(session, as_of, as_of + datetime.timedelta(days=horizon_days))

    if len(history) and not bool((history.kickoff_date < as_of_day).all()):
        latest = history.kickoff_date.max()
        raise LeakageError(
            f"live history reaches {latest}, which is on or after the as-of date "
            f"{as_of.isoformat()}; history must be strictly before it"
        )
    if len(fixtures) and not bool((fixtures.kickoff_date > as_of_day).all()):
        earliest = fixtures.kickoff_date.min()
        raise LeakageError(
            f"live fixtures start at {earliest}, which is on or before the as-of date "
            f"{as_of.isoformat()}; a fixture must be strictly after it"
        )

    rows: list[PredictedRow] = []
    counts: list[ModelCounts] = []
    for name in models:
        model = make_model(name, seed=seed)
        model.fit(history, cutoff=as_of)
        probabilities = model.predict(fixtures)
        description = model.describe()
        model_version = str(description["version"])
        model_config = dict(cast(Mapping[str, object], description["hyperparameters"]))

        n_rows = 0
        n_no_forecast = 0
        for index in range(len(fixtures)):
            forecast = probabilities[index]
            if bool(np.isnan(forecast).all()):
                n_no_forecast += 1
                continue
            rows.append(
                PredictedRow(
                    match_id=int(fixtures.match_id[index]),
                    model=str(description["name"]),
                    model_version=model_version,
                    model_config=model_config,
                    p_home=float(forecast[0]),
                    p_draw=float(forecast[1]),
                    p_away=float(forecast[2]),
                    odds_source=fixtures.odds_source[index],
                )
            )
            n_rows += 1
        counts.append(
            ModelCounts(
                model=name,
                n_fixtures=len(fixtures),
                n_rows=n_rows,
                n_no_forecast=n_no_forecast,
            )
        )

    return PredictionBatch(
        as_of=as_of,
        horizon_days=horizon_days,
        n_history=len(history),
        n_fixtures=len(fixtures),
        data_fingerprint=data_fingerprint(history),
        rows=tuple(rows),
        counts=tuple(counts),
    )
