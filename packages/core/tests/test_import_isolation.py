"""Guard: the modelling and evaluation packages must never import the database.

The modelled data reaches a model only through the arrays built by
:mod:`lmi_core.modeling.data`, which is the one module of the package allowed to
touch SQLAlchemy. A stray ``import lmi_core.db`` anywhere else would let a model
read the tables directly and defeat the leakage guards, so this test imports the
public modules in a fresh interpreter and fails when ``lmi_core.db`` or
``sqlalchemy`` shows up in ``sys.modules``.

The check runs in a subprocess because this test process has already imported
half the application: only a clean interpreter can prove what a module pulls in.
"""

import subprocess
import sys

MODULES = (
    "lmi_core.modeling",
    "lmi_core.modeling.base",
    "lmi_core.modeling.types",
    "lmi_core.modeling.registry",
    "lmi_core.modeling.baselines",
    "lmi_core.modeling.market",
    "lmi_core.modeling.elo",
    "lmi_core.modeling.dixon_coles",
    "lmi_core.evaluation.metrics",
    "lmi_core.evaluation.walkforward",
    "lmi_core.evaluation.backtest",
)
"""The modules whose import graph must not reach the database."""

_SCRIPT = f"""
import importlib
import sys

for name in {MODULES!r}:
    importlib.import_module(name)

forbidden = sorted(
    name
    for name in sys.modules
    if name == "lmi_core.db"
    or name.startswith("lmi_core.db.")
    or name == "sqlalchemy"
    or name.startswith("sqlalchemy.")
)
if forbidden:
    print("\\n".join(forbidden))
    raise SystemExit(1)
"""


def test_modeling_and_evaluation_import_without_the_database() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", _SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )

    offenders = result.stdout.strip() or result.stderr.strip()
    assert result.returncode == 0, (
        "these modules imported the database or sqlalchemy in a clean interpreter:\n" + offenders
    )
