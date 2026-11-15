"""Ingestion run endpoints: the recorded runs of the data loader.

The endpoints land in a later M4 task; this module only declares the router.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/ingestion-runs", tags=["ingestion-runs"])
