"""Tests for the Elo model: the replay, the newcomer rule and the ordered logit.

Every number here is synthetic or hand-computed. The synthetic league of the
shared harness supplies the walk-forward checks; the two small leagues below are
built row by row so that the instant a team debuts is unambiguous.

The assertions read the documented seams of the model (``replay_``,
``d_values_``, ``ratings_``, ``params_``) rather than its internals: they say
what the model must expose, not how it computes it.
"""

import dataclasses
import datetime
from collections.abc import Sequence

import numpy as np
import pytest
from lmi_core.evaluation.walkforward import walk_forward, weekly_cutoffs
from lmi_core.modeling import elo as elo_module
from lmi_core.modeling.elo import (
    EloModel,
    LogitParams,
    RatingStep,
    expected_score,
    newcomer_value,
    outcome_probabilities,
)
from lmi_core.modeling.registry import make_model
from lmi_core.modeling.types import Fixtures, History
from numpy.typing import NDArray

from modeling.leakage_harness import (
    assert_fit_predict_invariant,
    assert_walk_forward_invariant,
    fixtures_between,
    history_before,
    make_fixtures,
    make_history,
    make_synthetic_league,
    middle_cutoff,
    to_date,
)

SEED = 11
CUTOFF = datetime.date(2021, 1, 9)
UNKNOWN_TEAM = 999

# Hand-computed values of the single-match example: a gap of 0.15 and the
# home-win update it implies, written out so the test does not ask the model.
HAND_GAP = 0.15
HAND_EXPECTED_SCORE = 1.0 / (1.0 + 10.0**-0.15)
HAND_HOME_RATING = 1500.0 + 20.0 * (1.0 - HAND_EXPECTED_SCORE)
HAND_AWAY_RATING = 1500.0 - 20.0 * (1.0 - HAND_EXPECTED_SCORE)

SEASON_ONE_START = datetime.date(2020, 9, 5)
SEASON_TWO_START = datetime.date(2021, 9, 4)
AFTER_EVERYTHING = datetime.date(2021, 10, 1)
SMALL_CUTOFF = datetime.date(2020, 2, 1)


def _day(day: int) -> datetime.date:
    """Return a January 2020 date, for the small leagues built row by row."""
    return datetime.date(2020, 1, day)


def _league(rows: Sequence[tuple[datetime.date, int, int, int, int, int]]) -> History:
    """Build a history from ``(date, season, home, away, home_goals, away_goals)`` rows.

    Rows are taken in the given order, which must already ascend by date so the
    match ids follow the stored order.
    """
    n = len(rows)
    home_goals = np.array([row[4] for row in rows], dtype=np.int64)
    away_goals = np.array([row[5] for row in rows], dtype=np.int64)
    result: NDArray[np.int8] = np.where(
        home_goals > away_goals,
        np.int8(0),
        np.where(home_goals == away_goals, np.int8(1), np.int8(2)),
    ).astype(np.int8)
    return History(
        match_id=np.arange(1, n + 1, dtype=np.int64),
        kickoff_date=np.array([row[0] for row in rows], dtype="datetime64[D]"),
        season_id=np.array([row[1] for row in rows], dtype=np.int64),
        home_team_id=np.array([row[2] for row in rows], dtype=np.int64),
        away_team_id=np.array([row[3] for row in rows], dtype=np.int64),
        odds=np.full((n, 3), np.nan, dtype=np.float64),
        odds_source=tuple(None for _ in range(n)),
        home_goals=home_goals,
        away_goals=away_goals,
        result=result,
    )


def _season_one_rows() -> list[tuple[datetime.date, int, int, int, int, int]]:
    """Return one lopsided season of teams 1-4, one round of two matches a week.

    Team 1 wins everything and team 4 loses everything, so the four final ratings
    are clearly separated and team 4 is the weakest. The first round debuts all
    four teams at once, which keeps every newcomer value independent of which
    teams the candidate set of a later season holds.
    """

    def week(
        offset: int, *rows: tuple[int, int, int, int]
    ) -> list[tuple[datetime.date, int, int, int, int, int]]:
        return [
            (SEASON_ONE_START + datetime.timedelta(days=7 * offset), 1, home, away, hg, ag)
            for home, away, hg, ag in rows
        ]

    return [
        *week(0, (1, 2, 3, 0), (3, 4, 3, 0)),
        *week(1, (2, 3, 1, 2), (4, 1, 0, 2)),
        *week(2, (1, 3, 2, 0), (2, 4, 2, 0)),
        *week(3, (2, 1, 1, 3), (4, 3, 0, 2)),
        *week(4, (1, 3, 3, 1), (2, 4, 2, 0)),
        *week(5, (3, 2, 2, 1), (4, 1, 0, 2)),
    ]


def test_one_home_win_moves_the_ratings_by_the_hand_computed_amount() -> None:
    history = make_history([0])
    model = EloModel(seed=SEED)

    model.fit(history, CUTOFF)

    assert model.replay_[0].d == pytest.approx(HAND_GAP, rel=0.0, abs=1e-12)
    assert model.ratings_[1] == pytest.approx(HAND_HOME_RATING, rel=0.0, abs=1e-9)
    assert model.ratings_[2] == pytest.approx(HAND_AWAY_RATING, rel=0.0, abs=1e-9)
    assert float(expected_score(np.array([HAND_GAP]))[0]) == pytest.approx(
        HAND_EXPECTED_SCORE, rel=0.0, abs=1e-7
    )


def test_expected_score_is_the_logistic_function_of_the_gap() -> None:
    gaps = np.array([-1.0, 0.0, 1.0], dtype=np.float64)

    scores = expected_score(gaps)

    np.testing.assert_allclose(
        scores,
        np.array([1.0 / 11.0, 0.5, 10.0 / 11.0], dtype=np.float64),
        rtol=0.0,
        atol=1e-12,
    )
    assert float(expected_score(0.0)) == pytest.approx(0.5, rel=0.0, abs=1e-12)


def test_ordered_logit_rows_are_a_distribution_increasing_in_the_gap() -> None:
    data = make_synthetic_league(n_teams=4, n_seasons=2)
    cutoff = middle_cutoff(data)
    model = EloModel(seed=SEED)
    model.fit(history_before(data, cutoff), cutoff)
    params: LogitParams = model.params_
    assert params.b > 0.0
    assert params.c1 < params.c2
    grid = np.linspace(-3.0, 3.0, 61, dtype=np.float64)

    probabilities = outcome_probabilities(grid, params)

    assert probabilities.shape == (grid.size, 3)
    assert bool((probabilities >= 0.0).all())
    np.testing.assert_allclose(probabilities.sum(axis=1), np.ones(grid.size), rtol=0.0, atol=1e-12)
    assert bool((np.diff(probabilities[:, 0]) >= 0.0).all())


def test_the_likelihood_receives_the_gap_recorded_before_each_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _league([(_day(1), 1, 1, 2, 2, 0), (_day(2), 1, 2, 1, 0, 2)])
    captured: list[NDArray[np.float64]] = []
    original = elo_module.fit_params

    def spy(d: NDArray[np.float64], result: NDArray[np.int8]) -> LogitParams:
        captured.append(np.array(d, dtype=np.float64, copy=True))
        return original(d, result)

    monkeypatch.setattr(elo_module, "fit_params", spy)
    model = EloModel(seed=SEED)

    model.fit(history, SMALL_CUTOFF)

    # Hand-computed, never read back from the model: the first gap is the start
    # ratings, and the second comes from the ratings the first result moved.
    hand_computed = np.array(
        [HAND_GAP, (HAND_AWAY_RATING + 60.0 - HAND_HOME_RATING) / 400.0], dtype=np.float64
    )
    assert len(captured) == 1
    np.testing.assert_allclose(captured[0], hand_computed, rtol=0.0, atol=1e-12)
    np.testing.assert_allclose(model.d_values_, hand_computed, rtol=0.0, atol=1e-12)


def test_two_debutants_in_one_match_get_the_same_initial_rating() -> None:
    rows = [
        (_day(1), 1, 1, 2, 2, 0),
        (_day(2), 1, 2, 3, 2, 0),
        (_day(3), 1, 1, 3, 2, 0),
        (_day(4), 1, 4, 5, 2, 0),
    ]
    prefix = EloModel(seed=SEED)
    prefix.fit(_league(rows[:3]), _day(4))
    expected = sum(sorted(prefix.ratings_.values())[:3]) / 3
    model = EloModel(seed=SEED)

    model.fit(_league(rows), SMALL_CUTOFF)

    assert model.initial_ratings_[4] == pytest.approx(expected, rel=0.0, abs=1e-9)
    assert model.initial_ratings_[5] == pytest.approx(expected, rel=0.0, abs=1e-9)


def test_a_same_date_result_does_not_change_a_later_same_date_forecast_input() -> None:
    prefix = [(_day(1), 1, 1, 2, 2, 0), (_day(1), 1, 3, 4, 2, 0)]
    home_win = [*prefix, (_day(2), 1, 1, 2, 2, 0), (_day(2), 1, 5, 6, 2, 0)]
    away_win = [*prefix, (_day(2), 1, 1, 2, 0, 2), (_day(2), 1, 5, 6, 2, 0)]
    first = EloModel(seed=SEED)
    first.fit(_league(home_win), SMALL_CUTOFF)
    second = EloModel(seed=SEED)
    second.fit(_league(away_win), SMALL_CUTOFF)

    assert first.ratings_[1] != second.ratings_[1]
    assert first.initial_ratings_[5] == second.initial_ratings_[5]
    assert first.replay_[3].d == second.replay_[3].d
    assert first.d_values_[3] == second.d_values_[3]


def test_the_likelihood_sees_the_pre_match_gap_of_every_replay_step() -> None:
    data = make_synthetic_league(n_teams=4, n_seasons=2)
    cutoff = middle_cutoff(data)
    model = EloModel(seed=SEED)

    model.fit(history_before(data, cutoff), cutoff)

    steps: tuple[RatingStep, ...] = model.replay_
    recorded = np.array([step.d for step in steps], dtype=np.float64)
    expected = np.array(
        [(step.home_rating + 60.0 - step.away_rating) / 400.0 for step in steps],
        dtype=np.float64,
    )
    assert steps
    np.testing.assert_array_equal(recorded, expected)
    np.testing.assert_array_equal(model.d_values_, recorded)


def test_predict_time_newcomer_gets_the_newcomer_probability_row() -> None:
    data = make_synthetic_league(n_teams=4, n_seasons=1)
    cutoff = middle_cutoff(data)
    model = EloModel(seed=SEED)
    model.fit(history_before(data, cutoff), cutoff)
    fixtures = fixtures_between(data, cutoff)
    assert len(fixtures) > 0
    home_team_id = np.array(fixtures.home_team_id, dtype=np.int64)
    away_team_id = int(fixtures.away_team_id[0])
    home_team_id[0] = UNKNOWN_TEAM
    with_unknown_team: Fixtures = dataclasses.replace(fixtures, home_team_id=home_team_id)

    probabilities = model.predict(with_unknown_team)

    three_lowest = sorted(model.ratings_.values())[:3]
    expected_newcomer = sum(three_lowest) / len(three_lowest)
    assert model.newcomer_rating_ == pytest.approx(expected_newcomer, rel=0.0, abs=1e-12)
    assert abs(model.newcomer_rating_ - 1500.0) > 1e-6
    gap = (model.newcomer_rating_ + 60.0 - model.ratings_[away_team_id]) / 400.0
    expected_row = outcome_probabilities(np.array([gap], dtype=np.float64), model.params_)
    np.testing.assert_allclose(probabilities[0], expected_row[0], rtol=0.0, atol=1e-12)
    assert UNKNOWN_TEAM not in model.ratings_


def test_a_debutant_is_rated_from_the_weakest_teams_of_the_newest_season() -> None:
    season_one = _league(_season_one_rows())
    season_two = [
        (SEASON_TWO_START, 2, 5, 1, 0, 2),
        (SEASON_TWO_START, 2, 2, 3, 1, 1),
        (SEASON_TWO_START + datetime.timedelta(days=7), 2, 1, 2, 2, 0),
        (SEASON_TWO_START + datetime.timedelta(days=7), 2, 3, 5, 2, 1),
    ]
    reference = EloModel(seed=SEED)
    reference.fit(season_one, SEASON_TWO_START)
    assert reference.ratings_[4] < min(reference.ratings_[team] for team in (1, 2, 3))
    expected_from_newcomer_candidates = sum(reference.ratings_[team] for team in (1, 2, 3)) / 3
    expected_if_the_relegated_team_counted = sum(sorted(reference.ratings_.values())[:3]) / 3
    model = EloModel(seed=SEED)

    model.fit(_league([*_season_one_rows(), *season_two]), AFTER_EVERYTHING)

    assert model.initial_ratings_[5] == pytest.approx(
        expected_from_newcomer_candidates, rel=0.0, abs=1e-12
    )
    assert abs(model.initial_ratings_[5] - 1500.0) > 1e-6
    assert abs(model.initial_ratings_[5] - expected_if_the_relegated_team_counted) > 1e-6
    debut = next(step for step in model.replay_ if step.home_team_id == 5)
    assert debut.match_id == 13
    assert debut.home_rating == pytest.approx(model.initial_ratings_[5], rel=0.0, abs=1e-12)


def test_newcomer_value_averages_the_three_lowest_rated_candidates() -> None:
    ratings = {1: 1400.0, 2: 1450.0, 3: 1500.0, 4: 1600.0}

    assert newcomer_value(ratings, (1, 2, 3)) == pytest.approx(1450.0, rel=0.0, abs=1e-12)
    assert newcomer_value(ratings, (1, 2, 3, 4)) == pytest.approx(
        (1400.0 + 1450.0 + 1500.0) / 3.0, rel=0.0, abs=1e-12
    )
    assert newcomer_value(ratings, (1, 4)) == pytest.approx(1500.0, rel=0.0, abs=1e-12)
    assert newcomer_value(ratings, (1,)) == pytest.approx(1400.0, rel=0.0, abs=1e-12)


def test_newcomer_value_falls_back_to_the_start_rating_without_rated_candidates() -> None:
    assert newcomer_value({}, (1, 2)) == pytest.approx(1500.0, rel=0.0, abs=1e-12)
    assert newcomer_value({9: 1700.0}, (1, 2)) == pytest.approx(1500.0, rel=0.0, abs=1e-12)


def test_fit_and_predict_without_history() -> None:
    model = EloModel(seed=SEED)

    model.fit(make_history([]), CUTOFF)
    probabilities = model.predict(make_fixtures(3))

    assert probabilities.shape == (3, 3)
    assert np.isfinite(probabilities).all()
    np.testing.assert_allclose(probabilities.sum(axis=1), np.ones(3), rtol=0.0, atol=1e-9)
    assert model.ratings_ == {}
    assert model.initial_ratings_ == {}
    assert model.newcomer_rating_ == pytest.approx(1500.0, rel=0.0, abs=1e-12)
    assert model.d_values_.size == 0


def test_refitting_the_same_instance_matches_a_fresh_one() -> None:
    data = make_synthetic_league(n_teams=4, n_seasons=2)
    cutoff = middle_cutoff(data)
    fixtures = fixtures_between(data, cutoff)
    short_cutoff = to_date(data.kickoff_date[6])
    reused = EloModel(seed=SEED)
    reused.fit(history_before(data, short_cutoff), short_cutoff)
    assert reused.ratings_
    short_d_values = reused.d_values_
    fresh = EloModel(seed=SEED)
    fresh.fit(history_before(data, cutoff), cutoff)

    reused.fit(history_before(data, cutoff), cutoff)

    assert reused.d_values_.size > short_d_values.size
    assert reused.ratings_ == fresh.ratings_
    assert reused.initial_ratings_ == fresh.initial_ratings_
    assert reused.params_ == fresh.params_
    np.testing.assert_array_equal(reused.d_values_, fresh.d_values_)
    np.testing.assert_array_equal(reused.predict(fixtures), fresh.predict(fixtures))


def test_walk_forward_covers_the_league_without_a_missing_row() -> None:
    data = make_synthetic_league()
    cutoffs = weekly_cutoffs(to_date(data.kickoff_date[0]), to_date(data.kickoff_date[-1]))

    result = walk_forward(data, "elo", seed=SEED, cutoffs=cutoffs)

    assert len(result) > 0
    assert np.isfinite(result.probabilities).all()
    np.testing.assert_allclose(
        result.probabilities.sum(axis=1), np.ones(len(result)), rtol=0.0, atol=1e-9
    )


def test_fit_predict_invariance() -> None:
    assert_fit_predict_invariant("elo")


def test_walk_forward_invariance() -> None:
    assert_walk_forward_invariant("elo")


def test_describe_reports_the_fixed_hyperparameters() -> None:
    model = make_model("elo", seed=SEED)

    assert isinstance(model, EloModel)
    assert model.describe() == {
        "name": "elo",
        "version": "1",
        "hyperparameters": {
            "seed": SEED,
            "start_rating": 1500.0,
            "k_factor": 20.0,
            "home_advantage": 60.0,
            "rating_scale": 400.0,
            "newcomer_sample": 3,
        },
    }
