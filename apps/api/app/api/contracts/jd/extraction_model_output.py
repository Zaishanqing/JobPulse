"""Compatibility re-exports for the neutral model-output contract."""

from app.contracts.jd.extraction_model_output import (
    ModelCandidateRequirement,
    ModelEvidence,
    ModelExtractionOutput,
    ModelFact,
    ModelResponsibility,
    ModelSourcedText,
)

__all__ = [
    "ModelCandidateRequirement", "ModelEvidence", "ModelExtractionOutput",
    "ModelFact", "ModelResponsibility", "ModelSourcedText",
]
