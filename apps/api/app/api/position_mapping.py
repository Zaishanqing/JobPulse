from collections.abc import Mapping

from app.domain.positions import PositionSkill
from app.contexts.catalog import PositionRecord


def position_skill_from_data(raw: Mapping[str, object], default_level: str) -> PositionSkill:
    name = str(raw.get("skill_name") or raw.get("raw_skill") or "unknown")
    skill_id = str(raw.get("skill_id") or raw.get("normalized_skill_id") or "skill_" + name.lower().replace(" ", "_"))
    return PositionSkill(skill_id, name, str(raw.get("category", "未分类")), float(raw.get("weight", 0.1)), float(raw.get("confidence", 0.9)), str(raw.get("importance_level") or default_level), float(raw.get("trend_score", 0.0)), int(raw.get("evidence_count", 0)), str(raw["created_at"]) if raw.get("created_at") else None)


def position_skill_data(skill: PositionSkill) -> dict[str, object]:
    data: dict[str, object] = {"skill_id": skill.skill_id, "skill_name": skill.skill_name, "category": skill.category, "weight": skill.weight, "confidence": skill.confidence, "importance_level": skill.importance_level, "trend_score": skill.trend_score, "evidence_count": skill.evidence_count}
    if skill.created_at:
        data["created_at"] = skill.created_at
    return data


def position_data(item: PositionRecord, *, jd_count: int | None = None) -> dict[str, object]:
    data: dict[str, object] = {"position_id": item.position_id, "position_code": item.position_code, "position_name": item.position_name, "taxonomy_family_code": item.taxonomy_family_code, "taxonomy_family_name": item.taxonomy_family_name, "skill_domain_codes": list(item.skill_domain_codes), "definition": item.definition, "aliases": list(item.aliases), "include_when": list(item.include_when), "exclude_when": list(item.exclude_when), "confusable_with": list(item.confusable_with), "taxonomy_version": item.taxonomy_version, "lifecycle_status": item.lifecycle_status, "deprecated_at": item.deprecated_at.isoformat() if item.deprecated_at else None, "replaced_by": item.replaced_by, "sample_support_status": item.sample_support_status, "source_emerging_position_id": item.source_emerging_position_id, "core_responsibilities": list(item.core_responsibilities), "required_skills": [position_skill_data(value) for value in item.required_skills], "bonus_skills": [position_skill_data(value) for value in item.bonus_skills], "industry_scenarios": list(item.industry_scenarios), "status": item.status, "graph_onboarding_status": item.graph_onboarding_status, "created_at": item.created_at.isoformat() if item.created_at else None, "updated_at": item.updated_at.isoformat() if item.updated_at else None}
    if jd_count is not None:
        data["jd_count"] = jd_count
    return data
