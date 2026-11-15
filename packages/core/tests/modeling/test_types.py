"""Tests for :mod:`lmi_core.modeling.types`.

The validation rules are the contract a later task's loader relies on, so each
one is exercised on the synthetic league.
"""

import dataclasses
import datetime

import numpy as np
import pytest
from lmi_core.modeling.types import OUTCOMES, Fixtures, History, LeakageError, MatchData

from modeling.leakage_harness import make_history, make_synthetic_league, middle_cutoff, to_date


def _league() -> MatchData:
    """Return a small sorted league: four teams, one double round robin."""
    return make_synthetic_league(n_teams=4, n_seasons=1)


def test_outcomes_order_is_home_draw_away() -> None:
    assert OUTCOMES == ("H", "D", "A")


def test_leakage_error_is_a_runtime_error() -> None:
    assert issubclass(LeakageError, RuntimeError)


def test_match_data_is_a_history() -> None:
    assert MatchData is History


def test_history_is_a_fixtures_superset() -> None:
    league = _league()

    assert isinstance(league, Fixtures)
    assert len(league) == 12
    assert league.odds.shape == (12, 3)
    assert len(league.odds_source) == 12
    assert league.result.dtype == np.int8
    assert league.kickoff_date.dtype == np.dtype("datetime64[D]")


def test_history_requires_sorted_rows() -> None:
    league = _league()

    with pytest.raises(ValueError, match="sorted by"):
        dataclasses.replace(league, kickoff_date=league.kickoff_date[::-1])


def test_history_requires_equal_lengths() -> None:
    league = _league()

    with pytest.raises(ValueError, match="same length"):
        dataclasses.replace(league, season_id=league.season_id[:-1])


def test_history_requires_a_three_column_odds_matrix() -> None:
    league = _league()

    with pytest.raises(ValueError, match=r"odds must have shape \(12, 3\)"):
        dataclasses.replace(league, odds=league.odds[:, :2])


def test_history_requires_a_result_that_agrees_with_the_goals() -> None:
    league = _league()
    inconsistent = ((league.result.astype(np.intp) + 1) % 3).astype(np.int8)

    with pytest.raises(ValueError, match="agree with home_goals"):
        dataclasses.replace(league, result=inconsistent)


def test_fixtures_carry_no_outcome_columns() -> None:
    league = _league()
    mask = league.kickoff_date >= np.datetime64(middle_cutoff(league), "D")

    fixtures = league.as_fixtures(mask)

    assert type(fixtures) is Fixtures
    assert not isinstance(fixtures, History)
    assert not hasattr(fixtures, "result")
    assert not hasattr(fixtures, "home_goals")
    assert np.array_equal(fixtures.match_id, league.match_id[mask])
    assert np.array_equal(fixtures.odds, league.odds[mask])
    assert fixtures.odds_source == tuple(
        source for source, keep in zip(league.odds_source, mask, strict=True) if keep
    )


def test_select_returns_a_history_with_the_selected_rows() -> None:
    league = _league()
    cutoff = middle_cutoff(league)

    history = league.select(league.kickoff_date < np.datetime64(cutoff, "D"))

    assert isinstance(history, History)
    assert len(history) > 0
    assert len(history) < len(league)
    assert all(to_date(day) < cutoff for day in history.kickoff_date)
    assert np.array_equal(history.result, league.result[: len(history)])


def test_an_empty_selection_is_a_valid_history() -> None:
    league = _league()
    empty = league.select(np.zeros(len(league), dtype=np.bool_))

    assert len(empty) == 0
    assert empty.odds.shape == (0, 3)
    assert empty.odds_source == ()
    assert empty.as_fixtures(np.zeros(0, dtype=np.bool_)).match_id.size == 0


def test_a_mask_must_cover_every_row() -> None:
    league = _league()

    with pytest.raises(ValueError, match="mask must have shape"):
        league.select(np.ones(3, dtype=np.bool_))
    with pytest.raises(ValueError, match="mask must have shape"):
        league.as_fixtures(np.ones(3, dtype=np.bool_))


def test_make_history_keeps_every_row_on_one_sorted_date() -> None:
    history = make_history([0, 1, 2])

    assert len(history) == 3
    assert np.array_equal(history.result, np.array([0, 1, 2], dtype=np.int8))
    assert np.array_equal(history.home_goals, np.array([1, 1, 0], dtype=np.int64))
    assert np.array_equal(history.away_goals, np.array([0, 1, 1], dtype=np.int64))
    assert np.array_equal(
        history.kickoff_date,
        np.full(3, np.datetime64(datetime.date(2020, 1, 4), "D"), dtype="datetime64[D]"),
    )
