"""Match preview endpoints: the pre-kickoff view of one fixture.

The endpoints land in a later M4 task; this module only declares the router. The
paths are ``/matches/{match_id}/preview``, so the prefix is shared with the
matches router on purpose and the distinct tag keeps the documentation groups
apart.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/matches", tags=["match-preview"])
