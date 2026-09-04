"""Authoritative field value contract for CV extraction."""

from typing import Literal, TypeAlias

FIELD_CONTRACT_VERSION = "2.4"

DEGREE_LEVEL_VALUES = (
    "high_school", "associate", "bachelor", "master", "doctor", "postdoc", "unknown",
)
GENDER_VALUES = ("male", "female", "unknown")
LANGUAGE_PROFICIENCY_VALUES = (
    "native", "fluent", "professional", "conversational", "basic", "unknown",
)
SKILL_ITEM_TYPE_VALUES = (
    "programming_language", "framework", "library", "database", "tool", "platform",
    "methodology", "domain_knowledge", "soft_skill", "language", "other",
)
PROFICIENCY_VALUES = ("know", "familiar", "proficient", "expert", "unknown")
ALIGNMENT_VALUES = ("exact", "normalized_exact", "unresolved")
RESOLUTION_STATUS_VALUES = ("resolved", "ambiguous", "unresolved")
WORK_TYPE_VALUES = ("full_time", "internship", "part_time", "contract", "unknown")
WORK_STATUS_VALUES = ("employed", "unemployed", "student", "graduating", "unknown")
AWARD_LEVEL_VALUES = (
    "international", "national", "provincial", "municipal", "school", "other", "unknown",
)
CERTIFICATE_KIND_VALUES = (
    "professional_certification", "language_certification", "license", "competition_award", "other",
)
PERSONAL_FIELD_EVIDENCE_NAME_VALUES = (
    "current_location", "expected_location", "expected_position", "expected_salary", "work_status",
    "available_date",
)
EDUCATION_FIELD_EVIDENCE_NAME_VALUES = (
    "school", "college", "major", "degree", "date", "gpa", "gpa_scale", "location", "school_tag",
)
WORK_FIELD_EVIDENCE_NAME_VALUES = (
    "company", "position", "date", "department", "location", "work_type",
)
PROJECT_FIELD_EVIDENCE_NAME_VALUES = (
    "name", "date", "role", "affiliation",
)


DegreeLevel: TypeAlias = Literal[
    "high_school", "associate", "bachelor", "master", "doctor", "postdoc", "unknown",
]
Gender: TypeAlias = Literal["male", "female", "unknown"]
LanguageProficiency: TypeAlias = Literal[
    "native", "fluent", "professional", "conversational", "basic", "unknown",
]
SkillItemType: TypeAlias = Literal[
    "programming_language", "framework", "library", "database", "tool", "platform",
    "methodology", "domain_knowledge", "soft_skill", "language", "other",
]
Proficiency: TypeAlias = Literal["know", "familiar", "proficient", "expert", "unknown"]
Alignment: TypeAlias = Literal["exact", "normalized_exact", "unresolved"]
ResolutionStatus: TypeAlias = Literal["resolved", "ambiguous", "unresolved"]
WorkType: TypeAlias = Literal["full_time", "internship", "part_time", "contract", "unknown"]
WorkStatus: TypeAlias = Literal["employed", "unemployed", "student", "graduating", "unknown"]
AwardLevel: TypeAlias = Literal[
    "international", "national", "provincial", "municipal", "school", "other", "unknown",
]
CertificateKind: TypeAlias = Literal[
    "professional_certification", "language_certification", "license", "competition_award", "other",
]
PersonalFieldEvidenceName: TypeAlias = Literal[
    "current_location", "expected_location", "expected_position", "expected_salary", "work_status",
    "available_date",
]
EducationFieldEvidenceName: TypeAlias = Literal[
    "school", "college", "major", "degree", "date", "gpa", "gpa_scale", "location", "school_tag",
]
WorkFieldEvidenceName: TypeAlias = Literal[
    "company", "position", "date", "department", "location", "work_type",
]
ProjectFieldEvidenceName: TypeAlias = Literal[
    "name", "date", "role", "affiliation",
]
