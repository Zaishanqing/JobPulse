"""Versioned JD contracts shared by API and Infrastructure adapters."""

from app.contracts.jd.evidence import Evidence, EvidenceAlignment, StrictModel
from app.contracts.jd.extraction_v2 import (
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
from app.contracts.jd.normalization_v2 import (
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
