"""Stable serialization for publish-gate domain results."""

from app.domain.publishing import GateViolation, PublishGateResult
from app.domain.value_types import SerializedPayload


def publish_gate_errors(errors: tuple[GateViolation, ...]) -> list[SerializedPayload]:
    mapped: list[SerializedPayload] = []
    for error in errors:
        item: SerializedPayload = {"rule": error.rule, "message": error.message}
        if error.support_id is not None:
            item["support_id"] = error.support_id
        if error.relation_id is not None:
            item["relation_id"] = error.relation_id
        if error.task_ids:
            item["task_ids"] = list(error.task_ids)
        mapped.append(item)
    return mapped


def publish_gate_result(result: PublishGateResult) -> SerializedPayload:
    return {
        "allowed": result.allowed,
        "hard_gate_allowed": result.hard_gate_allowed,
        "errors": publish_gate_errors(result.errors),
        "valid_sample_count": result.valid_sample_count,
        "open_review_task_count": result.open_review_task_count,
        "unresolved_count": result.unresolved_count,
        "non_exact_evidence_count": result.non_exact_evidence_count,
        "low_confidence_relation_count": result.low_confidence_relation_count,
        "minimum_valid_samples": result.minimum_valid_samples,
        "minimum_samples_met": result.minimum_samples_met,
        "skill_profile_available": result.skill_profile_available,
        "task_profile_available": result.task_profile_available,
        "requirement_profile_available": result.requirement_profile_available,
    }
