"""The model-metrics payloads, re-exported from the results contract.

Every name here is a :mod:`lmi_core.results.schemas` type re-exported unchanged,
not redeclared. The results store, the worker's ``--format json`` output and
this API serve one shape, so a copy in the API would be a second contract that
drifts from the first; M3 decision D11 keeps the definition next to the store
and every caller imports it from there.

The module exists so the API imports its response model from its own schema
package, next to the payloads of the other resources, while the contract itself
stays where the store defines it. Each re-exported name is listed in
:data:`__all__`, which is also what keeps the imports from reading as unused.
"""

from lmi_core.results.schemas import (
    BacktestRunOut,
    CalibrationBin,
    MetricRow,
    ModelInfo,
    ModelMetricsResponse,
    ModelName,
)

__all__ = [
    "BacktestRunOut",
    "CalibrationBin",
    "MetricRow",
    "ModelInfo",
    "ModelMetricsResponse",
    "ModelName",
]
