# ruff: noqa: F401
"""Public entry point for the Emerging Positions bounded context."""

from importlib import import_module as _import_module

from app.contexts.emerging_positions.contracts import (
    ClusterRecord,
    DefinitionSelectionRecord,
    DefinitionVersionRecord,
    EmergingActor,
    EmergingChanges,
    EmergingRecord,
    GeneratedDefinitionRecord,
    GerminationAssessmentRecord,
    ReviewEmergingDefinitionCommand,
    ReleaseGateConfig,
    StandardPositionRecord,
)
from app.contexts.emerging_positions.ports import (
    DuplicateEmergingProjection,
    EmergingPositionRepository,
    EmergingPositionUnitOfWork,
)
from app.contexts.emerging_positions.contracts import __all__ as _contract_exports
from app.contexts.emerging_positions.ports import __all__ as _port_exports

_APPLICATION_EXPORTS = (
    "CreateEmergingCandidate",
    "DefinitionVersionNotFound",
    "DeleteEmergingCandidate",
    "DiscoveryEvidenceUnavailable",
    "EmergingClusterNotFound",
    "EmergingPositionHandlers",
    "EmergingPositionNotFound",
    "FormalExperimentImportRecord",
    "GenerateEmergingDefinition",
    "ImportFormalExperimentResults",
    "InvalidEmergingTransition",
    "PromoteEmergingCandidate",
    "PublishEmergingCandidate",
    "ReleaseGateRejected",
    "ReviewEmergingDefinition",
    "QueryDefinitionVersions",
    "QueryEmergingCandidates",
    "QueryGerminationAssessment",
    "SelectDefinitionVersion",
    "SubmitEmergingDefinition",
    "UpdateEmergingCandidate",
)


def __getattr__(name: str) -> type:
    if name not in _APPLICATION_EXPORTS:
        raise AttributeError(name)
    return getattr(_import_module("app.contexts.emerging_positions.application"), name)


__all__ = [*_APPLICATION_EXPORTS, *_contract_exports, *_port_exports]
