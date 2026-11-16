"""Monte Carlo season simulation: a pure, seeded engine with no I/O.

The engine turns finished results, the fixtures still to play and one scoreline
distribution per fixture into per-team distributions of final points and table
positions. It reads no clock, no database and no configuration, so the same
arguments always produce identical rows; the API and the CLI layer decide what
"the rest of the season" means and hand it over.

Importing this package only binds names.
"""

from typing import Final

from lmi_core.simulation.engine import (
    Qualification,
    SeasonSimulation,
    SimFixture,
    TeamSimulation,
    simulate_season,
)

DEFAULT_SEED: Final = 0
"""The seed a caller uses when it wants the default reproducibility guarantee."""

__all__ = [
    "DEFAULT_SEED",
    "Qualification",
    "SeasonSimulation",
    "SimFixture",
    "TeamSimulation",
    "simulate_season",
]
