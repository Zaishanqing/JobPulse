"""Compatibility imports for the Emerging Positions bounded context.

New code must import these types through ``app.contexts.emerging_positions``.
"""

from app.contexts.emerging_positions.application_types import (
    DefinitionSelectionRecord,
    EmergingChanges,
    GeneratedDefinitionRecord,
    GerminationAssessmentRecord,
)
from app.contexts.emerging_positions.domain import (
    ClusterRecord,
    DefinitionVersionRecord,
    EmergingActor,
    EmergingRecord,
    ReleaseGateConfig,
    StandardPositionRecord,
)
from app.contexts.emerging_positions.ports import (
    DuplicateEmergingProjection,
    EmergingPositionRepository,
    EmergingPositionUnitOfWork,
)

__all__ = [
    "ClusterRecord",
    "DefinitionSelectionRecord",
    "DefinitionVersionRecord",
    "DuplicateEmergingProjection",
    "EmergingActor",
    "EmergingChanges",
    "EmergingPositionRepository",
    "EmergingPositionUnitOfWork",
    "EmergingRecord",
    "GeneratedDefinitionRecord",
    "GerminationAssessmentRecord",
    "ReleaseGateConfig",
    "StandardPositionRecord",
]
