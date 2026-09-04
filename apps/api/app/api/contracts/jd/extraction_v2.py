"""Compatibility re-exports for the neutral V2 extraction contract."""

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

__all__ = [
    "CandidateRequirement", "CertificateRequirement", "CompanyFact",
    "CompanyFactKind", "EducationRequirement", "EmploymentFact",
    "EmploymentFactKind", "ExperienceRequirement", "JDExtractionResult",
    "Modality", "OtherRequirement", "SkillItem", "SkillItemType",
    "SkillRequirement", "SoftSkillRequirement", "SourcedText", "TaskRequirement",
]
