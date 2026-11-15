"""Prediction endpoints: the stored live predictions of a match.

The endpoints land in a later M4 task; this module only declares the router.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/predictions", tags=["predictions"])
