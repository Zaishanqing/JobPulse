from collections.abc import Mapping

from app.domain.values import freeze, thaw
from app.contexts.emerging_positions import (
    DefinitionSelectionRecord,
    DefinitionVersionRecord,
    EmergingChanges,
    EmergingRecord,
    GeneratedDefinitionRecord,
    GerminationAssessmentRecord,
    StandardPositionRecord,
    ReviewEmergingDefinitionCommand,
)


def _skill_name(value: Mapping[str, object]) -> str:
    return str(
        value.get("raw_skill")
        or value.get("skill_name")
        or value.get("name")
        or value.get("normalized_skill_id")
        or ""
    )


def _skills_with_evidence(
    skills: object, field_evidence: Mapping[str, object], field: str
) -> list[dict[str, object]]:
    field_data = field_evidence.get(field)
    items = field_data.get("items", []) if isinstance(field_data, Mapping) else []
    evidence_by_claim = {
        str(item.get("content") or ""): list(item.get("evidence") or [])
        for item in items
        if isinstance(item, Mapping)
    }
    result: list[dict[str, object]] = []
    for raw in thaw(skills):
        item = dict(raw) if isinstance(raw, Mapping) else {"raw_skill": str(raw)}
        evidence = evidence_by_claim.get(_skill_name(item), [])
        jd_ids = {
            str(entry.get("source_jd_id"))
            for entry in evidence
            if isinstance(entry, Mapping) and entry.get("source_jd_id")
        }
        sources = {
            str(entry.get("data_source"))
            for entry in evidence
            if isinstance(entry, Mapping) and entry.get("data_source")
        }
        item.update(
            {
                "support_jd_count": len(jd_ids),
                "support_source_count": len(sources),
                "evidence": evidence,
            }
        )
        result.append(item)
    return result


def emerging_record_data(record: EmergingRecord) -> dict[str, object]:
    item = record.candidate
    field_evidence = thaw(item.field_evidence)
    return {
        "emerging_id": item.candidate_id,
        "cluster_id": item.cluster_id,
        "position_name": item.position_name,
        "core_responsibilities": list(item.core_responsibilities),
        "required_skills": _skills_with_evidence(
            item.required_skills, field_evidence, "required_skills"
        ),
        "bonus_skills": _skills_with_evidence(
            item.bonus_skills, field_evidence, "bonus_skills"
        ),
        "industry_scenarios": list(item.industry_scenarios),
        "germination_score": item.germination_score,
        "score_dimensions": thaw(item.score_dimensions),
        "evidence_jd_ids": list(item.evidence_jd_ids),
        "field_evidence": field_evidence,
        "review_history": thaw(item.review_history),
        "published_snapshot": thaw(item.published_snapshot),
        "status": item.status.value,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "concept_type": "emerging_position",
        "concept_note": "新兴岗位基于真实招聘 JD 稳定簇；预测岗位基于政策、报告、论文等趋势信号，本批次不生成。",
        "standard_position": (
            standard_position_data(record.standard_position)
            if record.standard_position is not None
            else None
        ),
    }


def asset_with_definition(asset: dict, definition: dict) -> dict:
    result = dict(asset)
    fields = definition.get("field_evidence", asset["field_evidence"])
    result["asset_definition"] = {**asset["asset_definition"], **definition, "field_evidence": fields}
    for key in ("position_name", "core_responsibilities", "industry_scenarios", "field_evidence"):
        if key in definition:
            result[key] = definition[key]
    for key in ("required_skills", "bonus_skills"):
        if key in definition:
            result[key] = _skills_with_evidence(definition[key], fields, key)
    return result


def standard_position_data(record: StandardPositionRecord) -> dict[str, object]:
    return {
        "standard_position_id": record.standard_position_id,
        "position_name": record.position_name,
        "source_emerging_position_id": record.source_emerging_position_id,
        "status": record.status,
        "graph_onboarding_status": record.graph_onboarding_status,
        "required_skills": thaw(record.required_skills),
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


def definition_version_data(record: DefinitionVersionRecord) -> dict[str, object]:
    snapshot = thaw(record.snapshot)
    field_evidence = snapshot.get("field_evidence", {})
    if isinstance(field_evidence, Mapping):
        for field in ("position_summary", "distinguishing_features"):
            evidence_field = field_evidence.get(field)
            if field not in snapshot and isinstance(evidence_field, Mapping):
                content = evidence_field.get("content")
                if content not in (None, "", []):
                    snapshot[field] = content
    return {
        "version_id": record.version_id,
        "emerging_id": record.emerging_id,
        "snapshot": snapshot,
        "selected": record.selected,
        "created_by": record.created_by,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "implementation_status": "database_persisted_definition_snapshot",
    }


def assessment_data(record: GerminationAssessmentRecord) -> dict[str, object]:
    assessment = record.assessment
    evidence = thaw(assessment.evidence_package)
    score_components = evidence.get("score_components")
    if not isinstance(score_components, list):
        emergence_index = evidence.get("emergence_index") or {}
        dimensions = (
            emergence_index.get("dimensions", {})
            if isinstance(emergence_index, Mapping)
            else {}
        )
        score_components = (
            [
                {
                    "name": str(name),
                    **(
                        dict(component)
                        if isinstance(component, Mapping)
                        else {"normalized_value": component}
                    ),
                }
                for name, component in dimensions.items()
            ]
            if isinstance(dimensions, Mapping)
            else []
        )
    diagnostic_features = evidence.get("diagnostic_features", {})
    return {
        "emerging_id": record.emerging_id,
        "germination_score": assessment.score,
        "score_dimensions": thaw(assessment.dimensions),
        "dimensions": thaw(assessment.dimensions),
        "score_dimensions_status": "legacy_diagnostic_not_scored",
        "score_components": score_components,
        "diagnostic_features": diagnostic_features,
        "level": assessment.level,
        "qualified_as_emerging": assessment.qualified,
        "decision_reason": assessment.decision_reason,
        "qualification_basis": record.qualification_basis,
        "weights": evidence.get("weights", {}),
        "thresholds": evidence.get("thresholds", {}),
        "evidence_summary": evidence,
        "evidence_package": evidence,
        "formula_version": evidence.get(
            "formula_version", "emergence-index-v4-seven-dimensions"
        ),
        "algorithm_version": evidence.get("algorithm_version"),
        "discovery_run_id": record.discovery_run_id,
    }


def generated_definition_data(record: GeneratedDefinitionRecord) -> dict[str, object]:
    return {
        **emerging_record_data(record.record),
        "definition_version_id": record.definition_version_id,
        "generation_mode": record.generation_mode,
        "evidence_ids": list(record.evidence_ids),
    }


def definition_selection_data(record: DefinitionSelectionRecord) -> dict[str, object]:
    return {
        **definition_version_data(record.version),
        "definition": emerging_record_data(record.definition),
    }


def emerging_changes_from_data(raw: Mapping[str, object]) -> EmergingChanges:
    required = raw.get("required_skills")
    bonus = raw.get("bonus_skills")
    return EmergingChanges(
        frozenset(raw),
        position_name=str(raw["position_name"]) if raw.get("position_name") is not None else None,
        core_responsibilities=tuple(str(item) for item in raw["core_responsibilities"]) if raw.get("core_responsibilities") is not None else None,
        required_skills=tuple(freeze(item) for item in required) if isinstance(required, list) else None,
        bonus_skills=tuple(freeze(item) for item in bonus) if isinstance(bonus, list) else None,
        industry_scenarios=tuple(str(item) for item in raw["industry_scenarios"]) if raw.get("industry_scenarios") is not None else None,
        status=str(raw["status"]) if raw.get("status") is not None else None,
        field_evidence=(
            freeze(raw["field_evidence"])
            if isinstance(raw.get("field_evidence"), dict)
            else None
        ),
    )


def review_command_from_data(raw: Mapping[str, object]) -> ReviewEmergingDefinitionCommand:
    required = raw.get("required_skills")
    evidence = raw.get("field_evidence")
    responsibilities = raw.get("core_responsibilities")
    return ReviewEmergingDefinitionCommand(
        conclusion=str(raw["conclusion"]),
        reason=str(raw["reason"]),
        position_name=(str(raw["position_name"]) if raw.get("position_name") is not None else None),
        core_responsibilities=(
            tuple(str(item) for item in responsibilities)
            if isinstance(responsibilities, list)
            else None
        ),
        required_skills=(
            tuple(freeze(item) for item in required) if isinstance(required, list) else None
        ),
        field_evidence=freeze(evidence) if isinstance(evidence, dict) else None,
    )
