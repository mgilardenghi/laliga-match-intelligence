"""Application factory for the LMI API."""

from fastapi import FastAPI

from lmi_api.routes.health import router as health_router

__all__ = ["app", "create_app"]


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    application = FastAPI(
        title="LMI API",
        description="La Liga Match Intelligence: the only entry point to LMI data.",
    )
    application.include_router(health_router)
    return application


app = create_app()
