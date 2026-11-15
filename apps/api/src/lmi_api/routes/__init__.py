"""HTTP route modules for the LMI API.

Each resource module defines its router as
``APIRouter(prefix="/<kebab-path>", tags=["<kebab-path>"])``, so the resource
name appears once and the tag groups the endpoints in the documentation.

:data:`V1_ROUTERS` is the single shared line a new resource group edits:
:func:`lmi_api.main.create_app` mounts every entry under ``/v1`` in one loop, so
adding an endpoint group touches this tuple and the new module, nothing else.
"""

from fastapi import APIRouter

from lmi_api.routes import matches, seasons, standings, teams

V1_ROUTERS: tuple[APIRouter, ...] = (
    seasons.router,
    teams.router,
    matches.router,
    standings.router,
)
"""Every router served under the versioned prefix, in mounting order."""
