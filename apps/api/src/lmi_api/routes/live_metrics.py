"""Live metric endpoints: the in-play metrics of a running match.

The endpoints land in a later M4 task; this module only declares the router.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/live-metrics", tags=["live-metrics"])
