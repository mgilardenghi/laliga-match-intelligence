"""Backtest result persistence and the shared model-metrics contract.

The worker writes a run through :mod:`lmi_core.results.store`; the API reads it
from there and serves :class:`lmi_core.results.schemas.ModelMetricsResponse`.
Both sides import the DTOs, so no caller invents its own shape.

Importing this package has no side effects: no engine is created and no
connection is opened until a store function is called with a session.
"""
