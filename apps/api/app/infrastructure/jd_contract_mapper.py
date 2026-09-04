from app.domain.jd import Document, NormalizationResult
from app.domain.json_types import JsonObject
from app.contexts.jd_lifecycle import JDLegacyFields, JDSkillDTO
from app.infrastructure.jd_extraction_mapper import domain_to_extraction
from app.infrastructure.jd_normalization_mapper import domain_to_normalization


def to_api_dto(document: Document, normalization: NormalizationResult) -> JsonObject:
    extraction = domain_to_extraction(document, document.contract_version)
    normalized = domain_to_normalization(normalization, normalization.contract_version)
    return {
        "schema_version": extraction.schema_version,
        "normalization_schema_version": normalized.schema_version,
        "extraction_result": extraction.model_dump(mode="json"),
        "normalized_result": normalized.model_dump(mode="json"),
    }


def to_legacy_dto(
    document: Document,
    normalization: NormalizationResult,
    *,
    fallback_title: str,
) -> JDLegacyFields:
    normalized_by_requirement = {
        (item.raw_payload.get("requirement_id"), item.source_value): item
        for item in normalization.items
        if item.raw_payload.get("requirement_id")
    }
    required_skills: list[JDSkillDTO] = []
    bonus_skills: list[JDSkillDTO] = []
    for requirement in document.requirements:
        if requirement.kind != "skill":
            continue
        source_items = requirement.raw_payload.get("items", [])
        for item in source_items:
            source_name = item["name"]
            mapped = normalized_by_requirement.get(
                (requirement.requirement_id, source_name)
            )
            normalized_skill_id = mapped.skill_id if mapped is not None else None
            legacy = JDSkillDTO(
                raw_skill=str(source_name),
                normalized_skill_id=(
                    normalized_skill_id if isinstance(normalized_skill_id, str) else None
                ),
                confidence=(
                    0.9
                    if mapped is not None and mapped.resolution_status == "resolved"
                    else 0.0
                ),
                resolution_status=(
                    mapped.resolution_status if mapped is not None else "unresolved"
                ),
            )
            if requirement.modality == "required":
                required_skills.append(legacy)
            elif requirement.modality in ("preferred", "bonus"):
                bonus_skills.append(legacy)
            else:
                # unknown/invalid modality stays out of the legacy buckets and is
                # preserved in the versioned extraction payload for review.
                continue
    education = next((item for item in document.requirements if item.kind == "education"), None)
    experience = next((item for item in document.requirements if item.kind == "experience"), None)
    industry = next(
        (item for item in document.facts if item.scope == "company" and item.kind == "industry"),
        None,
    )
    return JDLegacyFields(
        position_title=document.title or fallback_title,
        responsibilities=tuple(
            str(item.raw_payload.get("action", "")) for item in document.responsibilities
        ),
        required_skills=tuple(required_skills),
        bonus_skills=tuple(bonus_skills),
        education=(
            str(education.raw_payload.get("minimum_degree"))
            if education and education.raw_payload.get("minimum_degree") is not None
            else None
        ),
        experience=(
            str(experience.raw_payload.get("duration_text"))
            if experience and experience.raw_payload.get("duration_text") is not None
            else None
        ),
        industry=industry.value if industry else None,
        tools=tuple(
            name
            for requirement in document.requirements
            if requirement.kind == "tool"
            for name in requirement.raw_payload.get("tools", [])
        )
        + tuple(
            item.raw_skill
            for item in required_skills + bonus_skills
            if item.raw_skill in {"Docker", "Spring Boot", "Kubernetes"}
        ),
    )
