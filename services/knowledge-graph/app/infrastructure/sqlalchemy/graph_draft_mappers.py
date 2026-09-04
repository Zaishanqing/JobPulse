"""Map persisted graph snapshots and copy rows to domain draft facts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.graph_drafts import (
    GraphDraftFacts,
    GraphDraftRelationFact,
    GraphDraftRequirementFact,
    GraphDraftSampleFact,
    GraphDraftSupportFact,
    GraphDraftTaskFact,
    GraphDraftVersionFact,
)
from app.models import (
    GraphBuildSample,
    GraphVersion,
    PositionSkillSupport,
    StandardPosition,
)


def _mapping(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _items(value: Any) -> list[dict[str, Any]]:
    return [deepcopy(item) for item in value] if isinstance(value, list) else []


def _relation(item: dict[str, Any]) -> GraphDraftRelationFact:
    auto_weight = float(item.get("auto_weight", item.get("weight", 0)))
    manual_weight = item.get("manual_weight")
    final_weight = float(item.get("final_weight", item.get("weight", 0)))
    auto_confidence = float(
        item.get("auto_confidence", item.get("confidence", 0))
    )
    manual_confidence = item.get("manual_confidence")
    final_confidence = float(
        item.get("final_confidence", item.get("confidence", 0))
    )
    auto_level = str(
        item.get("auto_importance_level", item.get("importance_level", "supplementary"))
    )
    manual_level = item.get("manual_importance_level")
    final_level = str(
        item.get("final_importance_level", item.get("importance_level", "supplementary"))
    )
    return GraphDraftRelationFact(
        str(item["skill_id"]),
        str(item.get("status") or ""),
        _mapping(item.get("metrics")),
        _mapping(item.get("statistics")),
        _mapping(item.get("explanation")),
        auto_weight,
        float(manual_weight) if manual_weight is not None else None,
        final_weight,
        auto_confidence,
        float(manual_confidence) if manual_confidence is not None else None,
        final_confidence,
        auto_level,
        str(manual_level) if manual_level is not None else None,
        final_level,
        float(item["trend_score"]) if item.get("trend_score") is not None else None,
    )


def _requirements(snapshot: dict[str, Any]) -> tuple[GraphDraftRequirementFact, ...]:
    result: list[GraphDraftRequirementFact] = []
    for item in _items(snapshot.get("requirement_profile")):
        payload = deepcopy(item)
        payload.pop("aggregate_id", None)
        result.append(GraphDraftRequirementFact(str(item["kind"]), payload))
    for group, kind in (
        ("company_context", "company_fact"),
        ("employment_context", "employment_fact"),
    ):
        for item in _items(snapshot.get(group)):
            payload = deepcopy(item)
            payload.pop("aggregate_id", None)
            payload.pop("kind", None)
            result.append(GraphDraftRequirementFact(kind, payload))
    return tuple(result)


def _version_fact(session: Session, version: GraphVersion | None):
    if version is None:
        return None
    snapshot = _mapping(version.snapshot)
    samples: tuple[GraphDraftSampleFact, ...] = ()
    supports: tuple[GraphDraftSupportFact, ...] = ()
    if version.build_run_id is not None:
        samples = tuple(
            GraphDraftSampleFact(
                item.document_id,
                item.included,
                tuple(item.exclusion_reasons or ()),
                item.effective_weight,
            )
            for item in session.scalars(
                select(GraphBuildSample).where(
                    GraphBuildSample.build_run_id == version.build_run_id
                )
            ).all()
        )
        supports = tuple(
            GraphDraftSupportFact(
                item.position_id,
                item.skill_id,
                item.document_id,
                item.requirement_id,
                item.normalized_skill_id,
                item.evidence_id,
                item.source_requirement_id,
                item.extraction_record_id,
                item.modality,
            )
            for item in session.scalars(
                select(PositionSkillSupport).where(
                    PositionSkillSupport.build_run_id == version.build_run_id
                )
            ).all()
        )
    relation_values = _items(snapshot.get("skill_relations"))
    if not relation_values:
        relation_values = _items(snapshot.get("skills"))
    task_values = _items(snapshot.get("responsibilities"))
    if not task_values:
        task_values = _items(snapshot.get("task_profile"))
    tasks: list[GraphDraftTaskFact] = []
    for item in task_values:
        payload = deepcopy(item)
        payload.pop("aggregate_id", None)
        tasks.append(GraphDraftTaskFact(payload))
    return GraphDraftVersionFact(
            version.id,
            version.position_id,
            version.published_at,
            version.build_run_id,
            _mapping(snapshot.get("algorithm_metadata")),
            _mapping(snapshot.get("sample_stats")),
            samples,
            supports,
            tuple(_relation(item) for item in relation_values),
            _requirements(snapshot),
            tuple(tasks),
    )


def load_graph_draft_facts(
    session: Session, position_id: str, base_version_id: int | None
) -> GraphDraftFacts:
    position = session.scalar(
        select(StandardPosition).where(StandardPosition.position_id == position_id)
    )
    if position is None:
        return GraphDraftFacts(position_id, False, None, None, None)
    current = (
        session.get(GraphVersion, position.current_version_id)
        if position.current_version_id is not None
        else None
    )
    requested = (
        session.get(GraphVersion, base_version_id)
        if base_version_id is not None
        else None
    )
    return GraphDraftFacts(
        position_id,
        True,
        position.current_version_id,
        _version_fact(session, current),
        _version_fact(session, requested),
    )
