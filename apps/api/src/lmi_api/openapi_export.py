"""Export the OpenAPI contract to a file.

The committed snapshot at ``packages/api-client/openapi.json`` is the input of the
dashboard's generated typed client: ``make api-client`` writes the document, then
runs the generator over it. The export is a build step, not a test helper, so it
must produce exactly the document the service serves at ``/openapi.json``.

The module needs no database and no network: it builds the application factory
only to read the specification.
"""

import json
import sys
from pathlib import Path

from lmi_api.main import create_app

__all__ = ["main", "render"]


def render() -> str:
    """Return the OpenAPI document in the canonical committed form."""
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    """Write the rendered document to ``argv[0]``.

    Returns ``0`` on success and ``2`` on a wrong argument count, so a caller can
    treat a misused command as a usage error rather than a failure to export.
    """
    if len(argv) != 1:
        print("usage: python -m lmi_api.openapi_export <path>", file=sys.stderr)
        return 2
    Path(argv[0]).write_text(render(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
