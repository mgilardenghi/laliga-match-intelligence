"""Model metric endpoints: the stored quality metrics of the fitted model.

The endpoints land in a later M4 task; this module only declares the router.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/model-metrics", tags=["model-metrics"])
