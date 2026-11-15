"""Simulation endpoints: the scenario runs of the season simulator.

The endpoints land in a later M4 task; this module only declares the router.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/simulations", tags=["simulations"])
