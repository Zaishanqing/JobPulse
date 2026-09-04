from app.domain.json_types import FrozenJsonObject
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.domain.accounts import AccountActor
from app.domain.feedback import (
    ALLOWED_FEEDBACK_STATUSES,
    ALLOWED_FEEDBACK_TYPES,
    FEEDBACK_REVIEW_ROLES,
    FeedbackConflict,
    FeedbackValidationError,
    require_feedback_creator,
)
from app.contexts.governance_feedback._ports.feedback import FeedbackRecord, FeedbackUnitOfWork
from app.domain.errors import PermissionDenied


class FeedbackNotFound(LookupError):
    pass


class FeedbackTargetNotFound(LookupError):
    pass


@dataclass(frozen=True)
class _TargetSpec:
    object_type: str
    id_fields: tuple[str, ...]


_TARGETS = {
    "resume_parse": _TargetSpec("resume", ("resume_id",)),
    "match_report": _TargetSpec("matching_evaluation", ("evaluation_id", "report_id")),
    "learning_path": _TargetSpec("learning_path", ("path_id", "evaluation_id")),
    "jd_parse": _TargetSpec("jd", ("jd_id",)),
    "skill_weight": _TargetSpec("enterprise_job", ("job_id", "enterprise_job_id")),
    "candidate_match": _TargetSpec(
        "matching_evaluation", ("evaluation_id", "report_id")
    ),
    "job_requirement_change": _TargetSpec(
        "enterprise_job", ("job_id", "enterprise_job_id")
    ),
}

_TRANSITIONS = {
    "pending_review": frozenset({"reviewing", "accepted", "rejected"}),
    "reviewing": frozenset({"accepted", "rejected"}),
    "accepted": frozenset(),
    "rejected": frozenset(),
}


@dataclass(frozen=True)
class ManageFeedback:
    uow_factory: Callable[[], FeedbackUnitOfWork]

    def create(
        self, actor: AccountActor, feedback_type: str, payload: FrozenJsonObject
    ) -> FeedbackRecord:
        require_feedback_creator(actor.role, feedback_type)
        normalized = self._normalize_target(feedback_type, payload)
        with self.uow_factory() as uow:
            target = uow.feedback.get_target(
                str(normalized["object_type"]), str(normalized["object_id"])
            )
            if target is None:
                raise FeedbackTargetNotFound("Feedback target not found")
            if target.owner_id != actor.account_id:
                raise PermissionDenied("Permission denied")
            duplicate = uow.feedback.find_open_duplicate(
                actor.account_id,
                str(normalized["object_type"]),
                str(normalized["object_id"]),
            )
            if duplicate is not None:
                raise FeedbackConflict("An unfinished feedback already exists")
            record = uow.feedback.add(feedback_type, actor.account_id, normalized)
            uow.commit()
            return record

    def get(self, actor: AccountActor, feedback_id: str) -> FeedbackRecord:
        with self.uow_factory() as uow:
            record = uow.feedback.get(feedback_id)
        if record is None:
            raise FeedbackNotFound("Feedback not found")
        if record.created_by != actor.account_id and actor.role not in FEEDBACK_REVIEW_ROLES:
            raise PermissionDenied("Permission denied")
        return record

    def list_page(
        self,
        actor: AccountActor,
        *,
        page: int,
        page_size: int,
        status: str | None,
        feedback_type: str | None,
    ) -> tuple[list[FeedbackRecord], int]:
        if status is not None and status not in ALLOWED_FEEDBACK_STATUSES:
            raise FeedbackValidationError("Invalid feedback status")
        if feedback_type is not None and feedback_type not in ALLOWED_FEEDBACK_TYPES:
            raise FeedbackValidationError("Invalid feedback type")
        with self.uow_factory() as uow:
            return uow.feedback.list_page(
                owner_id=(
                    None if actor.role in FEEDBACK_REVIEW_ROLES else actor.account_id
                ),
                status=status,
                feedback_type=feedback_type,
                offset=(page - 1) * page_size,
                limit=page_size,
            )

    def update(
        self, actor: AccountActor, feedback_id: str, changes: FrozenJsonObject
    ) -> FeedbackRecord:
        with self.uow_factory() as uow:
            current = uow.feedback.get(feedback_id)
            if current is None:
                raise FeedbackNotFound("Feedback not found")
            reviewer = actor.role in FEEDBACK_REVIEW_ROLES
            if current.created_by != actor.account_id and not reviewer:
                raise PermissionDenied("Permission denied")
            if current.status in {"accepted", "rejected"} and changes:
                raise FeedbackConflict("Finished feedback cannot be changed")

            payload = None
            status = None
            if "payload" in changes:
                raw_payload = changes["payload"]
                if not isinstance(raw_payload, dict):
                    raise FeedbackValidationError("payload must be an object")
                if not reviewer and current.status != "pending_review":
                    raise FeedbackConflict("Feedback under review cannot be edited")
                payload = {**current.payload, **raw_payload}
                self._ensure_same_target(current.payload, payload)
            if "status" in changes:
                if not reviewer:
                    raise PermissionDenied("Only reviewers can change status")
                if changes["status"] not in ALLOWED_FEEDBACK_STATUSES:
                    raise FeedbackValidationError("Invalid feedback status")
                status = str(changes["status"])
                if status not in _TRANSITIONS[current.status]:
                    raise FeedbackConflict("Invalid feedback status transition")
                payload = dict(payload or current.payload)
                payload["review_audit"] = {
                    "operator_id": actor.account_id,
                    "handled_at": datetime.now(timezone.utc).isoformat(),
                    "result": status,
                }
            record = uow.feedback.update(feedback_id, payload, status)
            uow.commit()
            return record

    @staticmethod
    def _normalize_target(
        feedback_type: str, payload: FrozenJsonObject
    ) -> FrozenJsonObject:
        spec = _TARGETS[feedback_type]
        raw_type = payload.get("object_type")
        if raw_type is not None and raw_type != spec.object_type:
            raise FeedbackValidationError("Invalid feedback object_type")
        ids = [payload.get("object_id"), *(payload.get(key) for key in spec.id_fields)]
        values = [value.strip() for value in ids if isinstance(value, str) and value.strip()]
        if not values:
            raise FeedbackValidationError("Feedback target ID is required")
        if len(set(values)) != 1:
            raise FeedbackValidationError("Feedback target IDs do not match")
        object_id = values[0]
        if spec.object_type == "learning_path":
            object_id = object_id.removeprefix("matching-service:")
        return {**payload, "object_type": spec.object_type, "object_id": object_id}

    @staticmethod
    def _ensure_same_target(current: FrozenJsonObject, updated: FrozenJsonObject) -> None:
        if (
            updated.get("object_type") != current.get("object_type")
            or updated.get("object_id") != current.get("object_id")
        ):
            raise FeedbackValidationError("Feedback target cannot be changed")
