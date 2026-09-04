from dataclasses import dataclass

from app.domain.policies import normalize_key
from app.domain.review_tasks import is_open_review_status

PUBLISHABLE_BUILD_STATUSES = frozenset(
    {"succeeded", "draft", "reviewing", "approved", "published"}
)

# Object-level effects that keep a draft in the published snapshot.
INCLUDED_OBJECT_STATUSES = frozenset({"approved", "auto_accepted"})

# Object-level effects that must be filtered out of the published snapshot.
EXCLUDED_OBJECT_STATUSES = frozenset({"rejected", "excluded"})


@dataclass(frozen=True)
class SupportViolation:
    support_id: int
    message: str


@dataclass(frozen=True)
class SupportIntegrityFact:
    """All persisted facts required to validate one graph support chain."""

    support_id: int
    support_document_id: str
    support_requirement_id: str
    support_skill_id: str
    skill_status: str | None
    normalized_exists: bool
    normalized_status: str | None
    normalized_skill_id: str | None
    normalized_source_name: str | None
    evidence_exists: bool
    evidence_document_id: str | None
    evidence_alignment: str | None
    evidence_start: int | None
    evidence_end: int | None
    evidence_quote: str | None
    document_exists: bool
    document_raw_text: str
    document_authority: str | None
    source_exists: bool
    source_document_id: str | None
    source_requirement_id: str | None
    source_kind: str | None
    source_item_names: tuple[str, ...]
    extraction_document_id: str | None


def validate_support_integrity(fact: SupportIntegrityFact) -> SupportViolation | None:
    """Evaluate support integrity without persistence or framework dependencies."""
    if fact.skill_status != "active":
        message = "skill_missing_or_inactive"
    elif not fact.normalized_exists:
        message = "normalized_skill_missing"
    elif fact.normalized_status not in ("resolved", "manually_confirmed"):
        message = "normalized_skill_unresolved"
    elif fact.normalized_skill_id != fact.support_skill_id:
        message = "normalized_skill_mismatch"
    elif not fact.evidence_exists:
        message = "evidence_missing"
    elif fact.evidence_document_id != fact.support_document_id:
        message = "evidence_wrong_document"
    elif not fact.document_exists:
        message = "evidence_slice_mismatch"
    elif fact.evidence_alignment not in (
        ("exact", "normalized_exact") if fact.document_authority == "authoritative" else ("exact",)
    ):
        message = "evidence_not_exact"
    elif fact.document_authority != "authoritative" and (
        fact.evidence_start is None
        or fact.evidence_end is None
        or fact.document_raw_text[fact.evidence_start : fact.evidence_end] != fact.evidence_quote
    ):
        message = "evidence_slice_mismatch"
    elif (
        not fact.source_exists
        or fact.source_document_id != fact.support_document_id
        or fact.source_requirement_id != fact.support_requirement_id
    ):
        message = "source_requirement_mismatch"
    elif fact.source_kind != "skill":
        message = "source_requirement_not_skill"
    elif normalize_key(fact.normalized_source_name or "") not in {
        normalize_key(name) for name in fact.source_item_names
    }:
        message = "source_skill_name_mismatch"
    elif fact.extraction_document_id != fact.support_document_id:
        message = "extraction_record_mismatch"
    else:
        return None
    return SupportViolation(fact.support_id, message)


@dataclass(frozen=True)
class RelationGateFact:
    relation_id: int
    status: str
    final_confidence: float
    unknown_ratio: float
    invalid_importance_level: bool = False
    invalid_modality: bool = False


@dataclass(frozen=True)
class ReviewTaskGateFact:
    task_id: int
    object_type: str
    status: str


@dataclass(frozen=True)
class PublishGateFacts:
    build_status: str
    valid_sample_count: int
    minimum_valid_samples: int
    position_active: bool
    supports: tuple[SupportIntegrityFact, ...]
    relations: tuple[RelationGateFact, ...]
    review_tasks: tuple[ReviewTaskGateFact, ...]
    unresolved_count: int
    non_exact_evidence_count: int
    requirement_aggregate_count: int
    task_aggregate_count: int


@dataclass(frozen=True)
class GateViolation:
    rule: str
    message: str
    support_id: int | None = None
    relation_id: int | None = None
    task_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class PublishGateResult:
    errors: tuple[GateViolation, ...]
    valid_sample_count: int
    open_review_task_count: int
    unresolved_count: int
    non_exact_evidence_count: int
    low_confidence_relation_count: int
    minimum_valid_samples: int
    skill_profile_available: bool = False
    task_profile_available: bool = False
    requirement_profile_available: bool = False

    @property
    def allowed(self) -> bool:
        return not self.errors

    @property
    def minimum_samples_met(self) -> bool:
        return self.valid_sample_count >= self.minimum_valid_samples

    @property
    def hard_gate_allowed(self) -> bool:
        hard_rules = {
            "build_status",
            "minimum_valid_samples",
            "position_status",
            "support_integrity",
            "non_empty_graph",
            "relation_approval",
            "profile_importance_invalid",
            "profile_modality_invalid",
            "unknown_modality",
            "snapshot_complete",
            "version_number",
            "version_unique",
            "version_name_unique",
        }
        return not any(
            error.rule in hard_rules for error in self.errors
        )


def evaluate_publish_gate(facts: PublishGateFacts) -> PublishGateResult:
    errors: list[GateViolation] = []
    included_relations = tuple(
        relation
        for relation in facts.relations
        if relation.status not in EXCLUDED_OBJECT_STATUSES
    )
    skill_profile_available = bool(included_relations)
    task_profile_available = facts.task_aggregate_count > 0
    requirement_profile_available = facts.requirement_aggregate_count > 0
    open_review_task_ids = tuple(
        task.task_id
        for task in facts.review_tasks
        if is_open_review_status(task.status)
    )
    rejected_overall_review_task_ids = tuple(
        task.task_id
        for task in facts.review_tasks
        if task.object_type == "graph_version"
        and task.status in {"rejected", "excluded"}
    )
    if facts.build_status not in PUBLISHABLE_BUILD_STATUSES:
        errors.append(GateViolation("build_status", "build status is not publishable"))
    if facts.valid_sample_count < facts.minimum_valid_samples:
        errors.append(GateViolation("minimum_valid_samples", "not enough valid samples"))
    if not facts.position_active:
        errors.append(GateViolation("position_status", "position is not publishable"))
    errors.extend(
        GateViolation("support_integrity", violation.message, support_id=violation.support_id)
        for item in facts.supports
        if (violation := validate_support_integrity(item)) is not None
    )
    if not (
        skill_profile_available
        or task_profile_available
        or requirement_profile_available
    ):
        errors.append(
            GateViolation(
                "non_empty_graph",
                "build has no valid relations, responsibilities, or requirements",
            )
        )
    if open_review_task_ids:
        errors.append(
            GateViolation(
                "open_review_tasks",
                "build has open review tasks",
                task_ids=open_review_task_ids,
            )
        )
    if rejected_overall_review_task_ids:
        errors.append(
            GateViolation(
                "graph_version_rejected",
                "overall graph review rejected this build",
                task_ids=rejected_overall_review_task_ids,
            )
        )
    for relation in included_relations:
        if relation.status not in {"approved", "auto_accepted"}:
            errors.append(
                GateViolation(
                    "relation_approval",
                    "relation is not approved",
                    relation_id=relation.relation_id,
                )
            )
        if relation.invalid_importance_level:
            errors.append(
                GateViolation(
                    "profile_importance_invalid",
                    "invalid profile importance level cannot be published",
                    relation_id=relation.relation_id,
                )
            )
        if relation.invalid_modality:
            errors.append(
                GateViolation(
                    "profile_modality_invalid",
                    "unknown profile modality cannot be published",
                    relation_id=relation.relation_id,
                )
            )
        if (
            relation.final_confidence < 0.7
            and relation.status not in {"approved", "auto_accepted"}
        ):
            errors.append(
                GateViolation(
                    "confidence_review",
                    "medium or low confidence requires review",
                    relation_id=relation.relation_id,
                )
            )
        if relation.unknown_ratio > 0:
            errors.append(
                GateViolation(
                    "unknown_modality",
                    "unknown modality cannot be published",
                    relation_id=relation.relation_id,
                )
            )
    return PublishGateResult(
        tuple(errors),
        facts.valid_sample_count,
        len(open_review_task_ids),
        facts.unresolved_count,
        facts.non_exact_evidence_count,
        sum(item.final_confidence < 0.7 for item in included_relations),
        facts.minimum_valid_samples,
        skill_profile_available,
        task_profile_available,
        requirement_profile_available,
    )
