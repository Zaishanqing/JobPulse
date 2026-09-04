"""Authoritative field value contract shared by Pydantic models and downstream services."""

from typing import Literal, TypeAlias


MODALITY_VALUES = ("required", "preferred", "bonus", "unknown")
ALIGNMENT_VALUES = ("exact", "normalized_exact", "unresolved")
PROFICIENCY_VALUES = ("know", "familiar", "proficient", "expert", "unknown")
SKILL_ITEM_TYPE_VALUES = (
    "programming_language",
    "framework",
    "library",
    "database",
    "tool",
    "platform",
    "methodology",
    "domain_knowledge",
    "other",
)
DEGREE_LEVEL_VALUES = (
    "high_school",
    "associate",
    "bachelor",
    "master",
    "doctor",
    "unknown",
)
COMPANY_FACT_KIND_VALUES = (
    "company_name",
    "industry",
    "development_stage",
    "company_size",
    "business_domain",
    "product_service",
    "target_market",
    "team",
    "technical_resource",
    "qualification",
    "other",
)
EMPLOYMENT_FACT_KIND_VALUES = (
    "location",
    "employment_type",
    "work_mode",
    "work_schedule",
    "social_security",
    "allowance",
    "meal",
    "accommodation",
    "health_check",
    "team_activity",
    "leave",
    "bonus",
    "commission",
    "equity",
    "training",
    "equipment",
    "other",
)
REQUIREMENT_KIND_VALUES = (
    "skill",
    "tool",
    "education",
    "experience",
    "certificate",
    "soft_skill",
    "other",
)
RESOLUTION_STATUS_VALUES = ("resolved", "ambiguous", "unresolved")
ADMISSION_TYPE_VALUES = ("unified", "non_unified", "unknown")
SALARY_STATUS_VALUES = ("specified", "negotiable", "not_disclosed")
SALARY_CURRENCY_VALUES = ("CNY",)
SALARY_PERIOD_VALUES = ("月", "日", "时", "周", "年")


Modality: TypeAlias = Literal["required", "preferred", "bonus", "unknown"]
Alignment: TypeAlias = Literal["exact", "normalized_exact", "unresolved"]
Proficiency: TypeAlias = Literal["know", "familiar", "proficient", "expert", "unknown"]
SkillItemType: TypeAlias = Literal[
    "programming_language",
    "framework",
    "library",
    "database",
    "tool",
    "platform",
    "methodology",
    "domain_knowledge",
    "other",
]
DegreeLevel: TypeAlias = Literal[
    "high_school", "associate", "bachelor", "master", "doctor", "unknown"
]
CompanyFactKind: TypeAlias = Literal[
    "company_name",
    "industry",
    "development_stage",
    "company_size",
    "business_domain",
    "product_service",
    "target_market",
    "team",
    "technical_resource",
    "qualification",
    "other",
]
EmploymentFactKind: TypeAlias = Literal[
    "location",
    "employment_type",
    "work_mode",
    "work_schedule",
    "social_security",
    "allowance",
    "meal",
    "accommodation",
    "health_check",
    "team_activity",
    "leave",
    "bonus",
    "commission",
    "equity",
    "training",
    "equipment",
    "other",
]
RequirementKind: TypeAlias = Literal[
    "skill", "tool", "education", "experience", "certificate", "soft_skill", "other"
]
CapabilityCategory: TypeAlias = SkillItemType
ResolutionStatus: TypeAlias = Literal["resolved", "ambiguous", "unresolved"]
AdmissionType: TypeAlias = Literal["unified", "non_unified", "unknown"]
SalaryStatus: TypeAlias = Literal["specified", "negotiable", "not_disclosed"]
SalaryCurrency: TypeAlias = Literal["CNY"]
SalaryPeriod: TypeAlias = Literal["月", "日", "时", "周", "年"]
