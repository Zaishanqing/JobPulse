from app.domain.json_types import thaw_json_object
from app.contexts.jd_lifecycle import JDParseResultDTO, JDSchemaView, JDSkillDTO


LEGACY_PARSE_FIELDS = (
    "position_title",
    "responsibilities",
    "required_skills",
    "bonus_skills",
    "education",
    "experience",
    "industry",
    "tools",
    "business_scenarios",
)


def parse_result_payload(
    result: JDParseResultDTO,
    schema_view: JDSchemaView | None = None,
) -> dict[str, object]:
    extraction_result = (
        schema_view.extraction_result
        if schema_view is not None
        else result.extraction_result
    )
    normalized_result = (
        schema_view.normalized_result
        if schema_view is not None
        else result.normalized_result
    )
    extraction_status = (
        schema_view.extraction_status
        if schema_view is not None
        else "available" if extraction_result is not None else "missing"
    )
    normalization_status = (
        schema_view.normalization_status
        if schema_view is not None
        else "available" if normalized_result is not None else "missing"
    )
    return {
        "parse_result_id": result.id,
        "jd_id": result.jd_id,
        "position_title": result.position_title,
        "responsibilities": list(result.responsibilities),
        "required_skills": [_skill_payload(item) for item in result.required_skills],
        "bonus_skills": [_skill_payload(item) for item in result.bonus_skills],
        "education": result.education,
        "experience": result.experience,
        "industry": result.industry,
        "tools": list(result.tools),
        "business_scenarios": list(result.business_scenarios),
        "parse_confidence": result.parse_confidence,
        "need_review": result.need_review,
        "schema_version": result.schema_version,
        "normalization_schema_version": result.normalization_schema_version,
        "extraction_result": (
            thaw_json_object(extraction_result) if extraction_result is not None else None
        ),
        "normalized_result": (
            thaw_json_object(normalized_result) if normalized_result is not None else None
        ),
        "execution": (
            thaw_json_object(result.execution_metadata)
            if result.execution_metadata is not None
            else None
        ),
        "extraction_status": extraction_status,
        "normalization_status": normalization_status,
        "workflow_status": result.workflow_status,
        "compatibility": {
            "legacy_fields": list(LEGACY_PARSE_FIELDS),
            "source": "versioned_domain_adapter",
        },
        "created_at": result.created_at.isoformat() if result.created_at else None,
        "updated_at": result.updated_at.isoformat() if result.updated_at else None,
    }


def _skill_payload(skill: JDSkillDTO) -> dict[str, object]:
    return {
        "raw_skill": skill.raw_skill,
        "normalized_skill_id": skill.normalized_skill_id,
        "confidence": skill.confidence,
        "resolution_status": skill.resolution_status,
    }
