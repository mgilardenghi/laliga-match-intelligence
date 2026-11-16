"""Tests for the live produce path: the guards and the batch contract.

The unit tests drive :func:`build_predictions` with synthetic columnar data and
no database. The integration tests run against the throwaway PostgreSQL of
:mod:`live_support` and prove the two leakage properties of the batch: the fit
sees only the past, and a future result cannot change a stored probability.
Every match, team and scoreline here is synthetic.
"""

import datetime
from typing import cast

import numpy as np
import pytest
from live_support import Database, database, store_match, store_team  # noqa: F401
from lmi_core.live import predict as predict_module
from lmi_core.modeling.types import Fixtures, History, LeakageError
from modeling.leakage_harness import make_fixtures, make_history
from numpy.typing import NDArray
from sqlalchemy.orm import Session


def _install_inputs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    history: History,
    fixtures: Fixtures,
) -> None:
    """Replace the module-level loaders with the supplied columnar inputs."""
    monkeypatch.setattr(predict_module, "load_history", lambda session, before: history)
    monkeypatch.setattr(predict_module, "load_upcoming", lambda session, after, until: fixtures)


@pytest.mark.parametrize("horizon_days", [0, 61])
def test_horizon_days_outside_the_bounds_is_rejected(horizon_days: int) -> None:
    with pytest.raises(ValueError, match="horizon_days"):
        predict_module.build_predictions(
            cast(Session, None),
            as_of=datetime.date(2026, 5, 13),
            horizon_days=horizon_days,
        )


def test_a_fixture_on_the_as_of_date_is_a_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    as_of = datetime.date(2021, 1, 10)
    _install_inputs(
        monkeypatch,
        history=make_history([0, 1, 2], kickoff_date=datetime.date(2020, 1, 4)),
        fixtures=make_fixtures(3, kickoff_date=as_of),
    )

    with pytest.raises(LeakageError, match="strictly after"):
        predict_module.build_predictions(cast(Session, None), as_of=as_of)


def test_history_on_the_as_of_date_is_a_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    as_of = datetime.date(2021, 1, 10)
    _install_inputs(
        monkeypatch,
        history=make_history([0, 1, 2], kickoff_date=as_of),
        fixtures=make_fixtures(3),
    )

    with pytest.raises(LeakageError, match="strictly before"):
        predict_module.build_predictions(cast(Session, None), as_of=as_of)


def test_market_without_odds_reports_no_forecast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    as_of = datetime.date(2021, 1, 10)
    _install_inputs(
        monkeypatch,
        history=make_history([0, 1, 2], kickoff_date=datetime.date(2020, 1, 4)),
        fixtures=make_fixtures(4, kickoff_date=datetime.date(2021, 1, 16)),
    )

    batch = predict_module.build_predictions(cast(Session, None), as_of=as_of, models=("market",))

    assert batch.rows == ()
    assert len(batch.counts) == 1
    assert batch.counts[0].n_rows == 0
    assert batch.counts[0].n_no_forecast == 4
    assert batch.counts[0].n_fixtures == batch.n_fixtures == 4


class _RecordingModel:
    """A fake model that keeps the inputs it was given and forecasts uniformly."""

    name = "fake"
    version = "test-1"

    def __init__(self, *, seed: int) -> None:
        self.seed = seed
        self.history: History | None = None
        self.fixtures: Fixtures | None = None
        self.cutoff: datetime.date | None = None

    def fit(self, history: History, cutoff: datetime.date) -> None:
        self.history = history
        self.cutoff = cutoff

    def predict(self, fixtures: Fixtures) -> NDArray[np.float64]:
        self.fixtures = fixtures
        return np.full((len(fixtures), 3), 1.0 / 3.0, dtype=np.float64)

    def describe(self) -> dict[str, object]:
        return {"name": self.name, "version": self.version, "hyperparameters": {}}


@pytest.mark.integration
def test_batch_fits_the_past_and_predicts_only_the_future(
    database: Database,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    as_of = datetime.date(2026, 5, 13)
    engine = database.engine
    third = store_team(engine, "Synthetic Third")
    fourth = store_team(engine, "Synthetic Fourth")
    home = database.home_team_id
    away = database.away_team_id

    played_id = store_match(
        engine,
        season_id=database.season_id,
        home_team_id=home,
        away_team_id=away,
        kickoff_date=as_of - datetime.timedelta(days=5),
        status="played",
        home_goals=1,
        away_goals=0,
    )
    at_as_of_id = store_match(
        engine,
        season_id=database.season_id,
        home_team_id=away,
        away_team_id=home,
        kickoff_date=as_of,
    )
    first_fixture_id = store_match(
        engine,
        season_id=database.season_id,
        home_team_id=third,
        away_team_id=fourth,
        kickoff_date=as_of + datetime.timedelta(days=2),
    )
    second_fixture_id = store_match(
        engine,
        season_id=database.season_id,
        home_team_id=fourth,
        away_team_id=third,
        kickoff_date=as_of + datetime.timedelta(days=4),
    )

    model = _RecordingModel(seed=0)
    monkeypatch.setattr(predict_module, "make_model", lambda name, *, seed: model)

    with Session(engine) as session:
        batch = predict_module.build_predictions(
            session, as_of=as_of, models=("fake",), horizon_days=7
        )

    assert model.history is not None
    assert model.fixtures is not None
    assert model.history.match_id.tolist() == [played_id]
    assert model.history.kickoff_date.max() < np.datetime64(as_of)
    assert bool((model.fixtures.kickoff_date > np.datetime64(as_of)).all())
    assert at_as_of_id not in model.fixtures.match_id.tolist()
    assert model.fixtures.match_id.tolist() == [first_fixture_id, second_fixture_id]
    assert batch.n_history == 1
    assert batch.n_fixtures == 2
    assert batch.counts[0].n_rows == 2
    assert batch.counts[0].n_no_forecast == 0


def _probabilities(
    batch: predict_module.PredictionBatch,
) -> dict[tuple[str, int], NDArray[np.float64]]:
    """Map ``(model, match_id)`` to the stored probability triple."""
    return {
        (row.model, row.match_id): np.array([row.p_home, row.p_draw, row.p_away])
        for row in batch.rows
    }


@pytest.mark.integration
def test_future_results_cannot_change_the_probabilities(database: Database) -> None:  # noqa: F811
    as_of = datetime.date(2026, 5, 13)
    engine = database.engine
    teams = [
        database.home_team_id,
        database.away_team_id,
        store_team(engine, "Synthetic Third"),
        store_team(engine, "Synthetic Fourth"),
        store_team(engine, "Synthetic Fifth"),
        store_team(engine, "Synthetic Sixth"),
    ]
    season = database.season_id

    store_match(
        engine,
        season_id=season,
        home_team_id=teams[0],
        away_team_id=teams[1],
        kickoff_date=as_of - datetime.timedelta(days=10),
        status="played",
        home_goals=2,
        away_goals=0,
    )
    store_match(
        engine,
        season_id=season,
        home_team_id=teams[1],
        away_team_id=teams[2],
        kickoff_date=as_of - datetime.timedelta(days=8),
        status="played",
        home_goals=0,
        away_goals=2,
    )
    store_match(
        engine,
        season_id=season,
        home_team_id=teams[2],
        away_team_id=teams[3],
        kickoff_date=as_of - datetime.timedelta(days=6),
        status="played",
        home_goals=1,
        away_goals=1,
    )
    store_match(
        engine,
        season_id=season,
        home_team_id=teams[3],
        away_team_id=teams[4],
        kickoff_date=as_of + datetime.timedelta(days=2),
    )
    store_match(
        engine,
        season_id=season,
        home_team_id=teams[4],
        away_team_id=teams[5],
        kickoff_date=as_of + datetime.timedelta(days=4),
    )

    models = ("home_win", "base_rate")
    with Session(engine) as session:
        clean = predict_module.build_predictions(
            session, as_of=as_of, horizon_days=10, models=models
        )

    # Absurd results on and after the as-of date, plus a scheduled match
    # outside the horizon: the first must stay invisible and the second must
    # not become a fixture of this batch.
    store_match(
        engine,
        season_id=season,
        home_team_id=teams[5],
        away_team_id=teams[0],
        kickoff_date=as_of,
        status="played",
        home_goals=9,
        away_goals=0,
    )
    store_match(
        engine,
        season_id=season,
        home_team_id=teams[0],
        away_team_id=teams[2],
        kickoff_date=as_of + datetime.timedelta(days=1),
        status="played",
        home_goals=0,
        away_goals=9,
    )
    store_match(
        engine,
        season_id=season,
        home_team_id=teams[1],
        away_team_id=teams[3],
        kickoff_date=as_of + datetime.timedelta(days=30),
    )

    with Session(engine) as session:
        poisoned = predict_module.build_predictions(
            session, as_of=as_of, horizon_days=10, models=models
        )

    clean_probabilities = _probabilities(clean)
    poisoned_probabilities = _probabilities(poisoned)
    assert clean_probabilities.keys() == poisoned_probabilities.keys()
    for key in clean_probabilities:
        assert np.array_equal(clean_probabilities[key], poisoned_probabilities[key])

    assert clean.n_history == poisoned.n_history == 3
    assert clean.n_fixtures == poisoned.n_fixtures == 2
    assert [count.n_rows for count in clean.counts] == [count.n_rows for count in poisoned.counts]
