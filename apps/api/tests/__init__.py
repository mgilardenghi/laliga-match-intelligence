"""Package marker for the API test suite.

The repository has three test roots, ``apps/api/tests``, ``apps/worker/tests``
and ``packages/core/tests``, and none of the others is a package. Both tools
that run the suite derive a module name from the file path, and without this
marker the API modules and the worker modules would share one:

- ``apps/api/tests/test_seasons.py`` and ``apps/worker/tests/test_seasons.py``
  would both be imported as ``test_seasons``, which pytest refuses with
  ``ImportError: import file mismatch`` before any test runs.
- ``apps/api/tests/conftest.py`` and ``apps/worker/tests/conftest.py`` would
  both be called ``conftest``, which mypy reports as a duplicate module and
  aborts the type check on.

With this file the API modules belong to the ``tests`` package and are imported
as ``tests.conftest`` and ``tests.test_seasons``, so a same-named module in
another test root is simply a different module. Sibling helpers such as
``factories`` are imported as ``tests.factories`` for the same reason.

The file is empty on purpose: it exists for the module name, not for code.
"""
