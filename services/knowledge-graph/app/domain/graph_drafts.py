"""Graph draft selection, idempotency, and copy planning."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from app.domain.decisions import DomainRejection
from app.domain.value_types import ExtensionAttributes, SerializedPayload


@dataclass(frozen=True)
class GraphDraftSampleFact:
    document_id: str
    included: bool
    exclusion_reasons: tuple[str, ...]
    effective_weight: float


@dataclass(frozen=True)
class GraphDraftSupportFact:
    position_id: str
    skill_id: str
    document_id: str
    requirement_id: str | None
    normalized_skill_id: int | None
    evidence_id: int | None
    source_requirement_id: int | None
    extraction_record_id: int | None
    modality: str | None


@dataclass(frozen=True)
class GraphDraftRelationFact:
    skill_id: str
    status: str
    metrics: ExtensionAttributes
    statistics: ExtensionAttributes
    explanation: ExtensionAttributes
    auto_weight: float
    manual_weight: float | None
    final_weight: float
    auto_confidence: float
    manual_confidence: float | None
    final_confidence: float
    auto_importance_level: str
    manual_importance_level: str | None
    final_importance_level: str
    trend_score: float | None


@dataclass(frozen=True)
class GraphDraftRequirementFact:
    kind: str
    payload: ExtensionAttributes


@dataclass(frozen=True)
class GraphDraftTaskFact:
    payload: ExtensionAttributes


@dataclass(frozen=True)
class GraphDraftVersionFact:
    version_id: int
    position_id: str
    published_at: datetime | None
    build_run_id: int | None
    algorithm_metadata: ExtensionAttributes
    sample_stats: ExtensionAttributes
    samples: tuple[GraphDraftSampleFact, ...] = ()
    supports: tuple[GraphDraftSupportFact, ...] = ()
    relations: tuple[GraphDraftRelationFact, ...] = ()
    requirements: tuple[GraphDraftRequirementFact, ...] = ()
    tasks: tuple[GraphDraftTaskFact, ...] = ()


@dataclass(frozen=True)
class GraphDraftFacts:
    position_id: str
    position_exists: bool
    current_version_id: int | None
    current_version: GraphDraftVersionFact | None
    requested_version: GraphDraftVersionFact | None


@dataclass(frozen=True)
class GraphDraftCommand:
    position_id: str
    base_version_id: int | None = None


@dataclass(frozen=True)
class GraphDraftCopyPlan:
    samples: tuple[GraphDraftSampleFact, ...]
    supports: tuple[GraphDraftSupportFact, ...]
    relations: tuple[GraphDraftRelationFact, ...]
    requirements: tuple[GraphDraftRequirementFact, ...]
    tasks: tuple[GraphDraftTaskFact, ...]


@dataclass(frozen=True)
class GraphDraftPlan:
    position_id: str
    base_version_id: int
    draft_key: str
    status: str
    window_start: datetime | None
    window_end: datetime | None
    config_snapshot: SerializedPayload
    summary: SerializedPayload
    copy: GraphDraftCopyPlan


@dataclass(frozen=True)
class GraphDraftDecision:
    accepted: bool
    plan: GraphDraftPlan | None = None
    rejection: DomainRejection | None = None


@dataclass(frozen=True)
class GraphDraftResult:
    build_run_id: int
    position_id: str
    base_version_id: int | None


def decide_graph_draft(
    facts: GraphDraftFacts, command: GraphDraftCommand
) -> GraphDraftDecision:
    if not facts.position_exists:
        return GraphDraftDecision(
            False, rejection=DomainRejection("not_found", "position not found")
        )
    selected_id = command.base_version_id or facts.current_version_id
    if selected_id is None:
        return GraphDraftDecision(
            False,
            rejection=DomainRejection(
                "conflict", "position has no published version to create a draft from"
            ),
        )
    version = (
        facts.requested_version
        if command.base_version_id is not None
        else facts.current_version
    )
    if version is None or version.version_id != selected_id:
        return GraphDraftDecision(
            False,
            rejection=DomainRejection(
                "validation", "base version does not belong to position"
            ),
        )
    if version.position_id != command.position_id:
        return GraphDraftDecision(
            False,
            rejection=DomainRejection(
                "validation", "base version does not belong to position"
            ),
        )
    config = {
        **dict(version.algorithm_metadata),
        "base_version_id": version.version_id,
        "draft_source": "published_snapshot",
    }
    config.setdefault("minimum_valid_samples", 1)
    summary = dict(version.sample_stats)
    if int(summary.get("included_samples", 0) or 0) < 1:
        summary["included_samples"] = 1
    return GraphDraftDecision(
        True,
        GraphDraftPlan(
            command.position_id,
            version.version_id,
            f"{command.position_id}:{version.version_id}",
            "draft",
            version.published_at,
            version.published_at,
            config,
            summary,
            GraphDraftCopyPlan(
                version.samples,
                version.supports,
                tuple(replace(item, status="approved") for item in version.relations),
                version.requirements,
                version.tasks,
            ),
        ),
    )
