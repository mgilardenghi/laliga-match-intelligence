"""Application factory for the LMI API."""

from fastapi import FastAPI
from fastapi.routing import APIRoute

from lmi_api.db import lifespan
from lmi_api.errors import install_error_handlers
from lmi_api.routes import V1_ROUTERS
from lmi_api.routes.health import router as health_router

__all__ = ["app", "create_app"]

V1_PREFIX = "/v1"
"""The prefix every versioned router is mounted under."""


def _operation_id(route: APIRoute) -> str:
    """Return the route function name as the OpenAPI ``operationId``.

    A generated client derives its method names from the operationId, so the
    names are readable by design: ``read_health``, ``read_version``,
    ``list_seasons``, ``list_teams`` and ``get_team``. The operationId must be
    unique across the document, so route function names must stay unique across
    the whole application; a duplicate would produce a specification a client
    generator rejects.
    """
    return route.name


def create_app() -> FastAPI:
    """Build the FastAPI application.

    The health routes stay at the root and every entry of
    :data:`~lmi_api.routes.V1_ROUTERS` is mounted under :data:`V1_PREFIX`, so a
    new resource group needs no edit here. The error handlers are installed
    before the routers, so no route is served with FastAPI's default error
    bodies.
    """
    application = FastAPI(
        title="LMI API",
        description="La Liga Match Intelligence: the only entry point to LMI data.",
        lifespan=lifespan,
        generate_unique_id_function=_operation_id,
    )
    install_error_handlers(application)
    application.include_router(health_router)
    for router in V1_ROUTERS:
        application.include_router(router, prefix=V1_PREFIX)
    return application


app = create_app()
