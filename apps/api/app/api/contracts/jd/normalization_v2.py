"""Compatibility re-exports for the neutral V2 normalization contract."""

from app.contracts.jd.normalization_v2 import (
    JDNormalizedResult,
    JobClassification,
    NormalizedSkill,
    SalaryNormalization,
    UnresolvedItem,
)

__all__ = [
    "JDNormalizedResult", "JobClassification", "NormalizedSkill",
    "SalaryNormalization", "UnresolvedItem",
]
