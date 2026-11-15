"""Guard: every stored model-name CHECK list agrees with the model registry.

``lmi_core.modeling.registry.MODEL_NAMES`` is the one list that decides which
models a backtest scores. The migration and the ORM repeat that list inside two
CHECK constraints, so a model added to the registry alone would be rejected by
the database only when a run tried to store its metric. This test compares all
four copies, in order, without a database: the migration is read as source
because the constraint it creates *is* that source text.
"""

import ast
import re
from pathlib import Path

from lmi_core.db.alembic_support import MIGRATIONS_DIR
from lmi_core.db.models import BacktestCalibration, BacktestMetric
from lmi_core.modeling.registry import MODEL_NAMES
from sqlalchemy import CheckConstraint

MIGRATION = MIGRATIONS_DIR / "versions" / "0002_backtest_results.py"
"""The revision that creates the two backtest tables with their model CHECK."""


def _is_call_to(node: ast.AST, attribute: str) -> bool:
    """Return whether ``node`` is a call to ``<anything>.<attribute>(...)``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attribute
    )


def _names_from_sqltext(sqltext: str) -> tuple[str, ...]:
    """Return the quoted names of a ``model IN (...)`` CHECK expression, in order."""
    match = re.fullmatch(r"model IN \((.*)\)", sqltext.strip())
    assert match is not None, f"unexpected model CHECK constraint: {sqltext!r}"
    return tuple(re.findall(r"'([^']*)'", match.group(1)))


def _migration_model_names(path: Path, table: str) -> tuple[str, ...]:
    """Return the model names of the ``model_valid`` CHECK that ``table`` declares."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not _is_call_to(node, "create_table"):
            continue
        assert isinstance(node, ast.Call)
        if (
            not node.args
            or not isinstance(node.args[0], ast.Constant)
            or node.args[0].value != table
        ):
            continue
        for argument in node.args[1:]:
            if not _is_call_to(argument, "CheckConstraint"):
                continue
            assert isinstance(argument, ast.Call)
            if not argument.args or not isinstance(argument.args[0], ast.Constant):
                continue
            sqltext = argument.args[0].value
            if isinstance(sqltext, str) and sqltext.startswith("model IN"):
                return _names_from_sqltext(sqltext)
    raise AssertionError(f"{path.name} has no model CHECK constraint for {table}")


def _orm_model_names(table: type[BacktestMetric] | type[BacktestCalibration]) -> tuple[str, ...]:
    """Return the model names of the ``model_valid`` CHECK in ``__table_args__``.

    The naming convention of :mod:`lmi_core.db.base` renders the declared name
    ``model_valid`` as ``ck_<table>_model_valid`` when the table is built, so the
    lookup accepts the rendered suffix instead of the bare name.
    """
    for entry in getattr(table, "__table_args__", ()):
        if isinstance(entry, CheckConstraint) and str(entry.name).endswith("model_valid"):
            return _names_from_sqltext(str(entry.sqltext))
    raise AssertionError(f"{table.__name__} has no model CHECK constraint")


def test_every_model_name_list_matches_the_registry() -> None:
    assert MIGRATION.is_file()

    assert _migration_model_names(MIGRATION, "backtest_metrics") == MODEL_NAMES
    assert _migration_model_names(MIGRATION, "backtest_calibration") == MODEL_NAMES
    assert _orm_model_names(BacktestMetric) == MODEL_NAMES
    assert _orm_model_names(BacktestCalibration) == MODEL_NAMES
