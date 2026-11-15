"""Team focus endpoints: the focused view of one team.

The endpoints land in a later M4 task; this module only declares the router. The
paths are ``/teams/{team_id}/focus``, so the prefix is shared with the teams
router on purpose and the distinct tag keeps the documentation groups apart.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/teams", tags=["team-focus"])
