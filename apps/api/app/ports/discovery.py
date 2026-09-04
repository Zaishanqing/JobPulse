"""Compatibility imports for the Discovery bounded context.

New code must import these types through ``app.contexts.discovery``.
"""

from app.contexts.discovery.application_types import ClusterJDRecord, ClusterProjection
from app.contexts.discovery.contracts import (
    DiscoveryClusterResult,
    DiscoveryRunRequest,
    DiscoveryRunResult,
)
from app.contexts.discovery.domain import Actor, ReleasedJDFact
from app.contexts.discovery.ports import (
    DiscoveryGateway,
    DiscoveryRepository,
    DiscoveryUnitOfWork,
)

__all__ = [
    "Actor",
    "ClusterJDRecord",
    "ClusterProjection",
    "DiscoveryClusterResult",
    "DiscoveryGateway",
    "DiscoveryRepository",
    "DiscoveryRunRequest",
    "DiscoveryRunResult",
    "DiscoveryUnitOfWork",
    "ReleasedJDFact",
]
