from app.domain.errors import PermissionDenied


PERSONAL_FEEDBACK_TYPES = frozenset({"resume_parse", "match_report", "learning_path"})
ENTERPRISE_FEEDBACK_TYPES = frozenset({"jd_parse", "skill_weight", "candidate_match", "job_requirement_change"})
FEEDBACK_REVIEW_ROLES = frozenset({"admin", "developer", "reviewer"})
ALLOWED_FEEDBACK_STATUSES = frozenset({"pending_review", "reviewing", "accepted", "rejected"})
ALLOWED_FEEDBACK_TYPES = PERSONAL_FEEDBACK_TYPES | ENTERPRISE_FEEDBACK_TYPES


class FeedbackValidationError(ValueError):
    pass


class FeedbackConflict(RuntimeError):
    pass


def require_feedback_creator(role: str, feedback_type: str) -> None:
    if feedback_type not in ALLOWED_FEEDBACK_TYPES:
        raise FeedbackValidationError("Invalid feedback type")
    expected = "personal_user" if feedback_type in PERSONAL_FEEDBACK_TYPES else "enterprise_user"
    if role != expected:
        raise PermissionDenied("Permission denied")
