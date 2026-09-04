"""Application orchestration and profile availability gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError

from app.domain.privacy import find_pii
from app.domain.profiles import CVMatchProfile, PositionMatchProfile, UnresolvedItem

ProfileT = TypeVar("ProfileT", CVMatchProfile, PositionMatchProfile)
ProfileStatus = Literal["ready", "review_required", "invalid"]


@dataclass(frozen=True)
class ValidationErrorItem:
    path: str
    message: str
    error_type: str


@dataclass(frozen=True)
class ProfileValidationResult:
    profile_status: ProfileStatus
    profile_id: str | None
    profile_version: str | None
    unresolved_items: tuple[UnresolvedItem, ...]
    validation_errors: tuple[ValidationErrorItem, ...]


def _contains_unresolved(value: object) -> bool:
    if isinstance(value, BaseModel):
        return _contains_unresolved(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return any(
            key == "resolution_status" and item in {"ambiguous", "unresolved"}
            or _contains_unresolved(item)
            for key, item in value.items()
        )
    if isinstance(value, tuple | list):
        return any(_contains_unresolved(item) for item in value)
    return False


class ProfileValidationService:
    """Parse, privacy-check, and gate matching profiles."""

    def validate_cv(self, payload: object) -> ProfileValidationResult:
        return self._validate(payload, CVMatchProfile)

    def validate_position(
        self,
        payload: object,
        *,
        technical_context_allowed: bool = False,
    ) -> ProfileValidationResult:
        return self._validate(
            payload,
            PositionMatchProfile,
            technical_context_allowed=technical_context_allowed,
        )

    def _validate(
        self,
        payload: object,
        profile_type: type[ProfileT],
        *,
        technical_context_allowed: bool = False,
    ) -> ProfileValidationResult:
        privacy_errors = tuple(
            ValidationErrorItem(item.path, item.reason, "pii_forbidden")
            for item in find_pii(
                payload,
                technical_context_allowed=technical_context_allowed,
            )
        )
        if privacy_errors:
            return ProfileValidationResult("invalid", None, None, (), privacy_errors)

        try:
            profile = profile_type.model_validate(payload)
        except ValidationError as exc:
            errors = tuple(
                ValidationErrorItem(
                    path=".".join(str(part) for part in item["loc"]) or "$",
                    message=item["msg"],
                    error_type=item["type"],
                )
                for item in exc.errors(include_url=False, include_input=False)
            )
            return ProfileValidationResult("invalid", None, None, (), errors)

        needs_review = (
            bool(profile.unresolved_items)
            or _contains_unresolved(profile)
            or profile.review_status != "approved"
            or (
                isinstance(profile, PositionMatchProfile)
                and profile.quality_context.status != "trusted"
            )
        )
        status: ProfileStatus = "review_required" if needs_review else "ready"
        return ProfileValidationResult(
            status,
            profile.profile_id,
            profile.profile_version,
            profile.unresolved_items,
            (),
        )
