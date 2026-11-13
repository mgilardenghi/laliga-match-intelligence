"""Contract tests for the health and version endpoints."""

import importlib.metadata

from fastapi.testclient import TestClient
from lmi_api.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version_reports_distribution_name_and_version() -> None:
    response = client.get("/version")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "lmi-api"
    assert payload["version"] == importlib.metadata.version("lmi-api")
