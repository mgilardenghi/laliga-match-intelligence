"""Tests for the OpenAPI snapshot export."""

import json
from pathlib import Path

import pytest
from lmi_api.openapi_export import main, render

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


def test_render_is_the_canonical_json_form() -> None:
    document = json.loads(render())

    assert render() == json.dumps(document, indent=2, sort_keys=True) + "\n"


def test_main_writes_the_rendered_document(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"

    assert main([str(target)]) == 0

    assert target.read_text(encoding="utf-8") == render()


def test_main_without_a_path_prints_usage_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "openapi.json"

    assert main([]) == 2

    captured = capsys.readouterr()
    assert "usage:" in captured.err
    assert not target.exists()


def test_operations_have_unique_ids_and_standings_is_exported() -> None:
    document = json.loads(render())
    paths = document["paths"]

    operation_ids = [
        item[method]["operationId"]
        for item in paths.values()
        for method in item
        if method in HTTP_METHODS
    ]

    assert len(operation_ids) == len(set(operation_ids))
    assert "/v1/standings" in paths
