"""Liveness and build-identity endpoints."""

import importlib.metadata

from fastapi import APIRouter
from pydantic import BaseModel

DISTRIBUTION_NAME = "lmi-api"

router = APIRouter()


class HealthResponse(BaseModel):
    """Liveness payload."""

    status: str


class VersionResponse(BaseModel):
    """Build identity of the running service."""

    name: str
    version: str


@router.get("/health", response_model=HealthResponse)
def read_health() -> HealthResponse:
    """Report that the service is up."""
    return HealthResponse(status="ok")


@router.get("/version", response_model=VersionResponse)
def read_version() -> VersionResponse:
    """Report the installed distribution name and version."""
    return VersionResponse(
        name=DISTRIBUTION_NAME,
        version=importlib.metadata.version(DISTRIBUTION_NAME),
    )
