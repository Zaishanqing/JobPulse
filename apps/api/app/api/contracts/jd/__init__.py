"""Versioned JD protocol contracts exposed to API adapters and integrations."""

from app.api.contracts.jd.evidence import Evidence, EvidenceAlignment, StrictModel
from app.api.contracts.jd.extraction_v2 import (
    CandidateRequirement,
    CertificateRequirement,
    CompanyFact,
    CompanyFactKind,
    EducationRequirement,
    EmploymentFact,
    EmploymentFactKind,
    ExperienceRequirement,
    JDExtractionResult,
    Modality,
    OtherRequirement,
    SkillItem,
    SkillItemType,
    SkillRequirement,
    SoftSkillRequirement,
    SourcedText,
    TaskRequirement,
)
from app.api.contracts.jd.normalization_v2 import (
    JDNormalizedResult,
    JobClassification,
    NormalizedSkill,
    SalaryNormalization,
    UnresolvedItem,
)

__all__ = [
    "CandidateRequirement", "CertificateRequirement", "CompanyFact",
    "CompanyFactKind", "EducationRequirement", "EmploymentFact",
    "EmploymentFactKind", "Evidence", "EvidenceAlignment",
    "ExperienceRequirement", "JDExtractionResult", "JDNormalizedResult",
    "JobClassification", "Modality", "NormalizedSkill", "OtherRequirement",
    "SalaryNormalization", "SkillItem", "SkillItemType", "SkillRequirement",
    "SoftSkillRequirement", "SourcedText", "StrictModel", "TaskRequirement",
    "UnresolvedItem",
]
