"""HTTP route modules for the LMI API.

Each resource module defines its router as
``APIRouter(prefix="/<kebab-path>", tags=["<kebab-path>"])``, so the resource
name appears once and the tag groups the endpoints in the documentation. A
resource whose paths live under another resource keeps that shared prefix and
the tag of that parent resource, so one entity stays one group in the
documentation: ``team_focus`` serves ``/teams`` with the ``teams`` tag and
``match_preview`` serves ``/matches`` with the ``matches`` tag.

:data:`V1_ROUTERS` is the single shared line a new resource group edits:
:func:`lmi_api.main.create_app` mounts every entry under ``/v1`` in one loop, so
adding an endpoint group touches this tuple and the new module, nothing else.
"""

from fastapi import APIRouter

from lmi_api.routes import (
    ingestion_runs,
    live_metrics,
    match_preview,
    matches,
    model_metrics,
    predictions,
    seasons,
    simulations,
    standings,
    team_focus,
    team_season_stats,
    teams,
)

V1_ROUTERS: tuple[APIRouter, ...] = (
    seasons.router,
    teams.router,
    matches.router,
    standings.router,
    predictions.router,
    live_metrics.router,
    simulations.router,
    model_metrics.router,
    ingestion_runs.router,
    team_focus.router,
    team_season_stats.router,
    match_preview.router,
)
"""Every router served under the versioned prefix, in mounting order."""
