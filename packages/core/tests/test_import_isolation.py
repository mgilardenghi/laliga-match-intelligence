"""Guards on what the public modules pull into a clean interpreter.

The modelled data reaches a model only through the arrays built by
:mod:`lmi_core.modeling.data`, which is the one module of the package allowed to
touch SQLAlchemy. A stray ``import lmi_core.db`` anywhere else would let a model
read the tables directly and defeat the leakage guards, so these tests import the
public modules in a fresh interpreter and fail when one of the forbidden module
prefixes shows up in ``sys.modules``.

:mod:`lmi_core.results` is the contract the API and the worker share. Its
converter is pure on purpose, which is what lets the API import it without a
database, a model or NumPy; the second test pins that.

The checks run in a subprocess because this test process has already imported
half the application: only a clean interpreter can prove what a module pulls in.
"""

import subprocess
import sys

MODELING_MODULES = (
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

RESULTS_MODULES = (
    "lmi_core.results",
    "lmi_core.results.schemas",
    "lmi_core.results.convert",
)
"""The shared results contract, which must import neither a model nor the database."""

_DATABASE_MODULES = ("lmi_core.db", "sqlalchemy")
"""The prefixes the modeling and evaluation packages must never import."""

_RESULTS_FORBIDDEN_MODULES = ("lmi_core.modeling", "lmi_core.db", "sqlalchemy")
"""The prefixes the results package must never import."""

_SCRIPT = """
import importlib
import sys

for name in {modules!r}:
    importlib.import_module(name)

prefixes = {forbidden!r}
offenders = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)
)
if offenders:
    print("\\n".join(offenders))
    raise SystemExit(1)
"""


def _imports_without(modules: tuple[str, ...], forbidden: tuple[str, ...]) -> None:
    """Assert that importing ``modules`` loads no module under ``forbidden``."""
    result = subprocess.run(
        [sys.executable, "-I", "-c", _SCRIPT.format(modules=modules, forbidden=forbidden)],
        capture_output=True,
        text=True,
        check=False,
    )

    offenders = result.stdout.strip() or result.stderr.strip()
    assert result.returncode == 0, (
        "these modules imported "
        + " or ".join(forbidden)
        + " in a clean interpreter:\n"
        + offenders
    )


def test_modeling_and_evaluation_import_without_the_database() -> None:
    _imports_without(MODELING_MODULES, _DATABASE_MODULES)


def test_the_results_contract_imports_neither_a_model_nor_the_database() -> None:
    _imports_without(RESULTS_MODULES, _RESULTS_FORBIDDEN_MODULES)
