"""In-process timing of a conditioned season simulation against a plain one.

The counting a caller asks for with ``condition_on`` happens once, after the last
run is played, over arrays the runs already produced. It must therefore stay
inside the noise of the simulation itself, and this test measures that: a 20-team,
380-fixture, 10 000-run season is simulated twice with the same arguments, once
plain and once conditioned on every team's next fixture, and the conditioned call
may cost no more than the plain one plus five per cent and half a second.

The test needs no database, no worker, no API and no stack of any kind: it runs
in process. It is marked ``integration`` on purpose. ``make test`` and the unit
step in CI select ``-m "not integration"``, and that marker is the only
deselector this repository already has; the alternative, a marker of its own in
``pyproject.toml``, would edit a file outside this task's scope. It is selected
on purpose with ``-m integration``.
"""

import datetime
import time
from collections.abc import Mapping, Sequence
from typing import Final

import numpy as np
import pytest
from lmi_core.simulation import Qualification, SimFixture, simulate_season
from numpy.typing import NDArray

pytestmark = pytest.mark.integration

N_TEAMS: Final = 20
"""A full league, the size the real season simulation uses."""

N_RUNS: Final = 10_000
"""The default run count of the season simulation."""

_START: Final = datetime.date(2026, 8, 15)


def season_fixtures(team_ids: Sequence[int]) -> list[SimFixture]:
    """Return every ordered pair of distinct teams, dated in list order.

    The list is already in ``(sim_date, match_id)`` order, which is the order the
    engine plays the fixtures in, so the scoreline rows keep their fixtures.
    """
    fixtures: list[SimFixture] = []
    for home in team_ids:
        for away in team_ids:
            if home != away:
                index = len(fixtures)
                fixtures.append(
                    SimFixture(
                        match_id=index + 1,
                        home_team_id=home,
                        away_team_id=away,
                        sim_date=_START + datetime.timedelta(days=index),
                    )
                )
    return fixtures


def flat_scorelines(n_fixtures: int) -> NDArray[np.float64]:
    """Return ``n_fixtures`` flat grids: every one of the 121 scorelines is as likely."""
    return np.full((n_fixtures, 11, 11), 1 / 121, dtype=np.float64)


def next_fixture_by_team(fixtures: Sequence[SimFixture], team_ids: Sequence[int]) -> dict[int, int]:
    """Map every team to the match id of the earliest fixture it plays."""
    return {
        team_id: next(
            fixture.match_id
            for fixture in fixtures
            if team_id in (fixture.home_team_id, fixture.away_team_id)
        )
        for team_id in team_ids
    }


def duration(
    teams: Mapping[int, str],
    fixtures: Sequence[SimFixture],
    score_probs: NDArray[np.float64],
    qualification: Qualification,
    condition_on: Mapping[int, int] | None,
) -> float:
    """Run one season simulation and return how many seconds it took."""
    start = time.perf_counter()
    simulation = simulate_season(
        teams,
        (),
        fixtures,
        score_probs,
        n_runs=N_RUNS,
        seed=0,
        qualification=qualification,
        condition_on=condition_on,
    )
    elapsed = time.perf_counter() - start
    assert simulation.n_runs == N_RUNS
    return elapsed


def test_conditioning_costs_no_measurable_share_of_the_run() -> None:
    team_ids = tuple(range(1, N_TEAMS + 1))
    teams = {team_id: f"Sim Town {team_id:02d}" for team_id in team_ids}
    fixtures = season_fixtures(team_ids)
    score_probs = flat_scorelines(len(fixtures))
    qualification = Qualification()
    condition_on = next_fixture_by_team(fixtures, team_ids)

    assert len(fixtures) == N_TEAMS * (N_TEAMS - 1) == 380
    assert set(condition_on) == set(team_ids)

    plain = duration(teams, fixtures, score_probs, qualification, None)
    conditioned = duration(teams, fixtures, score_probs, qualification, condition_on)
    print(f"plain {plain:.3f}s, conditioned {conditioned:.3f}s")

    assert conditioned <= plain * 1.05 + 0.5
