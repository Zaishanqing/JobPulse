"""Canonical mappers between domain values and recursive JSON contracts."""

from __future__ import annotations

from app.domain.discovery import (
    AlgorithmMetadata,
    GeneratedDefinition,
    GeneratedSkill,
    JDSnapshot,
    JDStructuredData,
    PositionReference,
    SkillReference,
)
from app.domain.values import FrozenDict, JsonObject


def normalize_snapshot(snapshot: JDSnapshot) -> JDSnapshot:
    if not isinstance(snapshot.structured_data, JDStructuredData):
        raise TypeError("snapshot structured_data must be JDStructuredData")
    return snapshot


def normalize_position_reference(reference: PositionReference) -> PositionReference:
    if not all(isinstance(item, SkillReference) for item in reference.required_skills):
        raise TypeError("position reference skills must be SkillReference values")
    return reference


def skill_reference_contract(raw_skill: str | None, normalized_skill_id: str | None) -> JsonObject:
    return FrozenDict(
        {
            key: value
            for key, value in {
                "raw_skill": raw_skill,
                "normalized_skill_id": normalized_skill_id,
            }.items()
            if value is not None
        }
    )


def structured_data_contract(snapshot: JDSnapshot) -> JsonObject:
    data = snapshot.structured_data
    values = dict(data.extensions)
    values.update(
        responsibilities=tuple(data.responsibilities),
        required_skills=tuple(
            skill_reference_contract(item.raw_skill, item.normalized_skill_id)
            for item in data.required_skills
        ),
        bonus_skills=tuple(
            skill_reference_contract(item.raw_skill, item.normalized_skill_id)
            for item in data.bonus_skills
        ),
        business_scenarios=tuple(data.business_scenarios),
    )
    if data.position_title is not None:
        values["position_title"] = data.position_title
    if data.industry is not None:
        values["industry"] = data.industry
    return FrozenDict(values)


def snapshot_contract(snapshot: JDSnapshot) -> JsonObject:
    return FrozenDict(
        {
            "source_fact_id": snapshot.source_fact_id,
            "source_fact_version": snapshot.source_fact_version,
            "content_hash": snapshot.content_hash,
            "window_id": snapshot.window_id,
            "jd_id": snapshot.jd_id,
            "schema_version": snapshot.schema_version,
            "review_status": snapshot.review_status,
            "consumption_path": snapshot.consumption_path,
            "title": snapshot.title,
            "source_name": snapshot.source_name,
            "publish_date": snapshot.publish_date.isoformat() if snapshot.publish_date else None,
            "structured_data": structured_data_contract(snapshot),
        }
    )


def position_reference_contract(reference: PositionReference) -> JsonObject:
    return FrozenDict(
        {
            "position_id": reference.position_id,
            "graph_version_id": reference.graph_version_id,
            "required_skills": tuple(
                skill_reference_contract(item.raw_skill, item.normalized_skill_id)
                for item in reference.required_skills
            ),
        }
    )


def algorithm_metadata_contract(metadata: AlgorithmMetadata) -> JsonObject:
    values = {
        "algorithm_name": metadata.algorithm_name,
        "requested_algorithm": metadata.requested_algorithm,
        "algorithm_version": metadata.algorithm_version,
        "feature_version": metadata.feature_version,
        "parameters": FrozenDict({"similarity_threshold": metadata.similarity_threshold}),
        "random_seed": metadata.random_seed,
        **dict(metadata.extensions),
    }
    return FrozenDict(values)


def generated_definition_contract(value: GeneratedDefinition) -> JsonObject:
    def skills(items: tuple[GeneratedSkill, ...]) -> tuple[JsonObject, ...]:
        return tuple(
            FrozenDict(
                {
                    "raw_skill": item.raw_skill,
                    "normalized_skill_id": item.normalized_skill_id,
                    "confidence": item.confidence,
                }
            )
            for item in items
        )

    return FrozenDict(
        {
            "position_name": value.position_name,
            "core_responsibilities": tuple(value.core_responsibilities),
            "required_skills": skills(value.required_skills),
            "bonus_skills": skills(value.bonus_skills),
            "industry_scenarios": tuple(value.industry_scenarios),
            "generation_mode": value.generation_mode,
            "field_evidence": value.field_evidence,
            "position_summary": value.position_summary,
            "distinguishing_features": tuple(value.distinguishing_features),
            "representative_enterprises": value.representative_enterprises,
            "growth_trajectory": tuple(value.growth_trajectory),
        }
    )
