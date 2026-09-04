from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.contexts.jd_lifecycle._ports.jd_repository import Actor, FileUpload
from app.domain.permissions import JD_CREATE, JD_PARSE, JD_PUBLISH, permissions_for_role


JD_READ_ROLES = {"enterprise_user", "admin", "developer", "reviewer"}
JD_ADMIN_ROLES = {"admin", "developer"}
JD_REVIEW_ROLES = {"reviewer", "admin", "developer"}
TASK_INTERNAL_ROLES = {"admin", "developer", "reviewer"}
class JDApplicationError(Exception):
    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


def _forbidden(detail: str) -> JDApplicationError:
    return JDApplicationError("forbidden", detail)


def _not_found(detail: str) -> JDApplicationError:
    return JDApplicationError("not_found", detail)


def _conflict(detail: str) -> JDApplicationError:
    return JDApplicationError("conflict", detail)


def _invalid(detail: str) -> JDApplicationError:
    return JDApplicationError("invalid", detail)


def _ensure_role(actor: Actor, allowed_roles: set[str], message: str) -> None:
    if actor.role not in allowed_roles:
        raise _forbidden(message)


def _ensure_can_read(actor: Actor) -> None:
    _ensure_role(actor, JD_READ_ROLES, "No permission to read JD data")


def _ensure_capability(actor: Actor, capability: str, message: str) -> None:
    if capability not in permissions_for_role(actor.role):
        raise _forbidden(message)


def _ensure_can_create(actor: Actor) -> None:
    _ensure_capability(actor, JD_CREATE, "No permission to create JD data")


def _ensure_can_write(actor: Actor) -> None:
    _ensure_capability(actor, JD_PARSE, "No permission to parse or update JD data")


def _ensure_can_review(actor: Actor) -> None:
    _ensure_role(actor, JD_REVIEW_ROLES, "No permission to review JD data")


def _ensure_can_publish(actor: Actor) -> None:
    _ensure_capability(actor, JD_PUBLISH, "No permission to publish JD data")


def _ensure_can_admin(actor: Actor) -> None:
    _ensure_role(actor, JD_ADMIN_ROLES, "No permission to manage JD data")


@dataclass(frozen=True)
class JDTextCreateCommand:
    source_type: str
    source_name: str | None
    enterprise_id: str | None
    title: str
    raw_text: str
    cleaned_text: str | None = None
    publish_date: date | None = None
    url: str | None = None


@dataclass(frozen=True)
class JDFileCreateCommand:
    upload: FileUpload
    title: str
    source_type: str
    source_name: str | None
    enterprise_id: str | None
    use_ocr: bool = False
