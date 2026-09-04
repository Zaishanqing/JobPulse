"""Resource authorization using trusted ownership and application grants."""

from __future__ import annotations

from collections.abc import Mapping

from app.application.authorization import API_ROLES, require_any_role
from app.domain.auth import (
    ENTERPRISE_ROLES,
    PERSONAL_ROLES,
    SERVICE_ROLES,
    AuthContext,
)
from app.domain.tasks import EvaluationTask
from app.ports.resource_authorization import (
    ApplicationGrantPort,
    CVAuthorizationPort,
    EnterpriseJobGrantPort,
)


class ResourceNotFoundError(Exception):
    """Deliberately conceal whether an unauthorized resource exists."""

    code = "RESOURCE_NOT_FOUND"
    message = "resource was not found"


class ResourceAuthorizationService:
    def __init__(
        self,
        cv_authorization: CVAuthorizationPort,
        application_grants: ApplicationGrantPort,
        enterprise_job_grants: EnterpriseJobGrantPort | None = None,
    ) -> None:
        self._cv_authorization = cv_authorization
        self._application_grants = application_grants
        self._enterprise_job_grants = enterprise_job_grants

    def require_business_role(self, context: AuthContext) -> None:
        require_any_role(context, API_ROLES)

    def authorize_cv(self, context: AuthContext, cv_id: object) -> None:
        self.require_business_role(context)
        if context.roles & SERVICE_ROLES:
            return
        if not isinstance(cv_id, str) or not cv_id.strip():
            return
        if context.roles & PERSONAL_ROLES and self._cv_authorization.is_owner(context, cv_id):
            return
        raise ResourceNotFoundError

    def authorize_match(
        self, context: AuthContext, cv_id: object, position_id: object,
        target_type: str | None = None,
    ) -> None:
        self.require_business_role(context)
        if context.roles & SERVICE_ROLES:
            return
        if not isinstance(cv_id, str) or not isinstance(position_id, str):
            return
        if context.roles & PERSONAL_ROLES:
            if self._cv_authorization.is_owner(context, cv_id):
                return
            raise ResourceNotFoundError
        if context.roles & ENTERPRISE_ROLES:
            if target_type == "enterprise_job" and self._enterprise_job_grants is not None:
                if self._enterprise_job_grants.has_active_grant(context, cv_id, position_id):
                    return
            elif self._application_grants.has_active_grant(context, cv_id, position_id):
                return
            raise ResourceNotFoundError
        raise ResourceNotFoundError

    def authorize_payload(self, context: AuthContext, payload: object) -> None:
        self.require_business_role(context)
        if not isinstance(payload, Mapping):
            return
        cv = payload.get("cv_profile")
        position = payload.get("position_profile")
        cv_id = cv.get("cv_id") if isinstance(cv, Mapping) else payload.get("cv_id")
        position_id = (
            position.get("position_id")
            if isinstance(position, Mapping)
            else payload.get("position_id")
        )
        target_type = (
            payload.get("target_type")
            if isinstance(payload.get("target_type"), str)
            else None
        )
        if cv_id is not None and position_id is not None:
            self.authorize_match(context, cv_id, position_id, target_type=target_type)
        elif cv_id is not None:
            self.authorize_cv(context, cv_id)

    def authorize_task(self, context: AuthContext, task: EvaluationTask) -> None:
        self.authorize_match(
            context, task.cv_profile.cv_id, task.position_profile.position_id,
            target_type=getattr(task, "target_type", None),
        )
