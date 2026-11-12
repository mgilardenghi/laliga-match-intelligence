"""Module entry point: run the worker with ``python -m lmi_worker``."""

import logging

from lmi_worker.cli import main

logging.basicConfig(level=logging.INFO)

raise SystemExit(main())
