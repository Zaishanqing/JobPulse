from pydantic import model_validator

from jobgraph_contracts.extraction_v2 import (
    Alignment,
    CandidateRequirement,
    CertificateRequirement,
    CompanyFact,
    EducationRequirement,
    EmploymentFact,
    Evidence,
    ExperienceRequirement,
    JDExtractionResult as SharedJDExtractionResult,
    Modality,
    OtherRequirement,
    RequirementBase,
    SkillItem,
    SkillRequirement,
    SoftSkillRequirement,
    SourcedText,
    TaskRequirement,
    TextRequirement,
)


class JDExtractionResult(SharedJDExtractionResult):
    @model_validator(mode="after")
    def no_normalized_fields(self):
        forbidden = {
            "skill_id",
            "canonical_name",
            "job_family",
            "category_code",
            "subcategory_code",
        }

        def walk(value):
            if isinstance(value, dict):
                if forbidden.intersection(value):
                    raise ValueError("normalized fields are forbidden in extraction layer")
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(self.model_dump())
        return self
