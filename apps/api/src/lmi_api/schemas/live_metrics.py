"""The live-metrics payloads, re-exported from the live scoring contract.

Every name here is a :mod:`lmi_core.live.schemas` type re-exported unchanged,
not redeclared. The scorer that aggregates the metrics, the worker's
``predict --report`` output and this API serve one shape, so a copy in the API
would be a second contract that drifts from the first; the live DTOs stay next
to :mod:`lmi_core.live.scoring`, which is the only producer of the numbers they
carry.

The module exists so the API imports its response model from its own schema
package, next to the payloads of the other resources, while the contract itself
stays where the scorer defines it. Each re-exported name is listed in
:data:`__all__`, which is also what keeps the imports from reading as unused.
"""

from lmi_core.live.schemas import (
    CommonSubset,
    LiveMetricsResponse,
    MarketReference,
    MetricScores,
    ModelMetrics,
    PredictionCounts,
)

__all__ = [
    "CommonSubset",
    "LiveMetricsResponse",
    "MarketReference",
    "MetricScores",
    "ModelMetrics",
    "PredictionCounts",
]
