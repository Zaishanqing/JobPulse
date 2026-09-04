from src.application.mappers import to_contract_extraction
from src.models import (
    CertificateRequirement,
    EducationRequirement,
    Evidence,
    ExperienceRequirement,
    JDExtractionResult,
    SoftSkillRequirement,
)


def _evidence(quote: str) -> Evidence:
    return Evidence(source_id="JD-STRUCTURED", quote=quote)


def test_non_skill_requirement_structure_survives_shared_contract_mapping():
    result = JDExtractionResult(
        document_id="JD-STRUCTURED",
        requirements=[
            EducationRequirement(
                requirement_id="education-1",
                kind="education",
                modality="required",
                evidence=_evidence("本科及以上，计算机相关专业"),
                minimum_degree="bachelor",
                majors=["计算机相关专业"],
            ),
            ExperienceRequirement(
                requirement_id="experience-1",
                kind="experience",
                modality="required",
                evidence=_evidence("3-5 年 Python 后端经验"),
                minimum_years=3,
                maximum_years=5,
                role="Python 后端",
                duration_text="3-5 年",
            ),
            CertificateRequirement(
                requirement_id="certificate-1",
                kind="certificate",
                modality="preferred",
                evidence=_evidence("持有软考证书优先"),
                certificates=["软考证书"],
            ),
            SoftSkillRequirement(
                requirement_id="soft-skill-1",
                kind="soft_skill",
                modality="required",
                evidence=_evidence("具备沟通协作能力"),
                skills=["沟通", "协作"],
            ),
        ],
    )

    contract = to_contract_extraction(result)
    values = {
        item.kind: item.model_dump(mode="json")
        for item in contract.requirements
    }

    assert values["education"]["minimum_degree"] == "bachelor"
    assert values["education"]["majors"] == ["计算机相关专业"]
    assert values["experience"]["minimum_years"] == 3
    assert values["experience"]["maximum_years"] == 5
    assert values["certificate"]["certificates"] == ["软考证书"]
    assert values["soft_skill"]["skills"] == ["沟通", "协作"]
    assert all(values[kind]["text"] for kind in values)
