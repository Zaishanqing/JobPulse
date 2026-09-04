from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.json_types import JsonObject
from app.domain.matching import MatchingRuleViolation


STANDARD_POSITION_SPECIALTY_ROUTE_GRAPH_VERSION = (
    "standard-position-specialty-routes.v2"
)
STANDARD_POSITION_PROFILE_INSUFFICIENT = "STANDARD_POSITION_PROFILE_INSUFFICIENT"
NO_FORMAL_REQUIREMENTS = "NO_FORMAL_REQUIREMENTS"
ROUTE_SUPPORT_UNAVAILABLE = "ROUTE_SUPPORT_UNAVAILABLE"
NO_VALID_SPECIALTY_ROUTE = "NO_VALID_SPECIALTY_ROUTE"


class MatchingContractNotFound(LookupError):
    pass


class MatchingContractUnavailable(RuntimeError):
    pass


class StandardPositionProfileInsufficient(
    MatchingRuleViolation, MatchingContractUnavailable
):
    """A published standard position cannot supply a reliable route graph."""

    code = STANDARD_POSITION_PROFILE_INSUFFICIENT
    message = (
        "standard position does not have sufficient evidence to build a reliable "
        "matching route"
    )

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(self.message)


class MatchingContractReader(Protocol):
    def cv_profile(
        self, cv_id: str, snapshot_id: str | None = None
    ) -> JsonObject | None: ...
    def position_profile(self, position_id: str) -> JsonObject | None: ...
    def is_cv_owner(self, subject_id: str, cv_id: str) -> bool: ...
    def has_application_grant(
        self, subject_id: str, tenant_id: str, cv_id: str, position_id: str
    ) -> bool: ...
    def enterprise_job_profile(self, job_id: str) -> JsonObject | None: ...
    def has_enterprise_job_grant(
        self, subject_id: str, tenant_id: str, cv_id: str, enterprise_job_id: str
    ) -> bool: ...
    def skill_relations(self, skill_ids: tuple[str, ...]) -> JsonObject: ...


@dataclass(frozen=True)
class MatchingContractService:
    reader: MatchingContractReader

    def cv_profile(self, cv_id: str, snapshot_id: str | None = None) -> JsonObject:
        value = self.reader.cv_profile(cv_id, snapshot_id=snapshot_id)
        if value is None:
            raise MatchingContractNotFound("contract resource was not found")
        return value

    def position_profile(self, position_id: str) -> JsonObject:
        value = self.reader.position_profile(position_id)
        if value is None:
            raise MatchingContractNotFound("contract resource was not found")
        return value

    def cv_owner(self, subject_id: str, cv_id: str) -> bool:
        return self.reader.is_cv_owner(subject_id, cv_id)

    def application_grant(
        self, subject_id: str, tenant_id: str, cv_id: str, position_id: str
    ) -> bool:
        return self.reader.has_application_grant(
            subject_id, tenant_id, cv_id, position_id
        )

    def enterprise_job_profile(self, job_id: str) -> JsonObject:
        value = self.reader.enterprise_job_profile(job_id)
        if value is None:
            raise MatchingContractNotFound("enterprise job contract resource was not found")
        return value

    def enterprise_job_grant(
        self, subject_id: str, tenant_id: str, cv_id: str, enterprise_job_id: str
    ) -> bool:
        return self.reader.has_enterprise_job_grant(
            subject_id, tenant_id, cv_id, enterprise_job_id
        )

    def skill_relations(self, skill_ids: tuple[str, ...]) -> JsonObject:
        return self.reader.skill_relations(skill_ids)
