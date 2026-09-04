"""End-to-end orchestration from upstream contract Ports to learning paths."""

from __future__ import annotations

from collections.abc import Mapping

from app.application.contract_mapping import (
    map_authoritative_cv_profile,
    map_authoritative_position_profile,
)
from app.application.evaluation import MatchEvaluationService
from app.application.learning_paths import LearningPathService
from app.domain.integration import ContractIntegrationResult, ContractIssue
from app.ports.profile_sources import CVProfileSource, PositionProfileSource
from app.ports.upstream_contracts import UpstreamResponseError, UpstreamTimeoutError


class ContractIntegrationService:
    def __init__(
        self,
        cv_source: CVProfileSource,
        position_source: PositionProfileSource,
        evaluation_service: MatchEvaluationService,
        learning_path_service: LearningPathService,
    ) -> None:
        self._cv_source = cv_source
        self._position_source = position_source
        self._evaluation_service = evaluation_service
        self._learning_path_service = learning_path_service

    def run(self, payload: object) -> ContractIntegrationResult:
        if not isinstance(payload, Mapping):
            return self._rejected("INTEGRATION_REQUEST_INVALID", "request must be an object")
        cv_id = payload.get("cv_id")
        position_id = payload.get("position_id")
        if not isinstance(cv_id, str) or not cv_id.strip():
            return self._rejected("CV_REFERENCE_MISSING", "cv_id is required")
        if not isinstance(position_id, str) or not position_id.strip():
            return self._rejected("POSITION_REFERENCE_MISSING", "position_id is required")
        try:
            cv_payload = self._cv_source.fetch_cv_profile(cv_id)
            target_type = payload.get("target_type", "standard_position")
            position_payload = (
                self._position_source.fetch_enterprise_job_profile(position_id)
                if target_type == "enterprise_job"
                else self._position_source.fetch_position_profile(position_id)
            )
        except UpstreamTimeoutError as exc:
            return self._transport_rejection("UPSTREAM_TIMEOUT", str(exc))
        except UpstreamResponseError as exc:
            return self._transport_rejection(
                "UPSTREAM_HTTP_ERROR",
                f"upstream status={exc.status_code}: {exc}",
            )
        except KeyError as exc:
            return self._transport_rejection(
                "UPSTREAM_CONTRACT_NOT_FOUND", f"upstream reference not found: {exc.args[0]}"
            )
        except Exception as exc:  # pragma: no cover - final adapter boundary
            return self._transport_rejection(
                "UPSTREAM_FETCH_FAILED", f"upstream fetch failed: {type(exc).__name__}"
            )
        cv_mapping = map_authoritative_cv_profile(cv_payload)
        position_mapping = map_authoritative_position_profile(position_payload)
        issues = cv_mapping.issues + position_mapping.issues
        versions = cv_mapping.versions + position_mapping.versions
        if cv_mapping.value is None or position_mapping.value is None:
            return ContractIntegrationResult(
                integration_status="rejected",
                contract_issues=issues,
                source_versions=versions,
                error_code="UPSTREAM_CONTRACT_INCOMPATIBLE",
                error_message="one or more upstream contracts could not be mapped",
            )
        cv = cv_mapping.value
        position = position_mapping.value
        try:
            evaluation = self._evaluation_service.evaluate(
                {
                    "cv_profile": cv.model_dump(mode="json"),
                    "position_profile": position.model_dump(mode="json"),
                    "tenant_ref": payload.get("tenant_ref"),
                    "target_type": payload.get("target_type", "standard_position"),
                    "use_enterprise_weights": payload.get("use_enterprise_weights", False),
                }
            )
        except UpstreamTimeoutError as exc:
            return self._transport_rejection("UPSTREAM_TIMEOUT", str(exc))
        except UpstreamResponseError as exc:
            return self._transport_rejection(
                "UPSTREAM_HTTP_ERROR",
                f"upstream status={exc.status_code}: {exc}",
            )
        if evaluation.evaluation_status != "completed":
            issue = ContractIssue(
                side="transport",
                code="MAPPED_PROFILE_NOT_READY",
                path="$.evaluation",
                message=evaluation.error_code or "mapped profiles failed evaluation gate",
                severity="error",
                source_version=evaluation.algorithm_version,
            )
            return ContractIntegrationResult(
                integration_status="rejected",
                cv_profile=cv,
                position_profile=position,
                evaluation=evaluation,
                contract_issues=(*issues, issue),
                source_versions=versions,
                error_code="MAPPED_PROFILE_NOT_READY",
                error_message="mapped profiles did not pass the evaluation gate",
            )
        gap = self._learning_path_service.generate(
            {"evaluation": evaluation.model_dump(mode="json")}
        )
        if gap.generation_status != "completed":
            issue = ContractIssue(
                side="transport",
                code="LEARNING_PATH_GENERATION_REJECTED",
                path="$.gap_analysis",
                message=gap.error_code or "learning-path generation was rejected",
                severity="error",
                source_version=gap.algorithm_version,
            )
            return ContractIntegrationResult(
                integration_status="rejected",
                cv_profile=cv,
                position_profile=position,
                evaluation=evaluation,
                gap_analysis=gap,
                contract_issues=(*issues, issue),
                source_versions=versions,
                error_code="LEARNING_PATH_GENERATION_REJECTED",
                error_message="mapped evaluation could not generate a learning path",
            )
        return ContractIntegrationResult(
            integration_status="completed",
            cv_profile=cv,
            position_profile=position,
            evaluation=evaluation,
            gap_analysis=gap,
            contract_issues=issues,
            source_versions=versions,
        )

    def resolve_task_payload(
        self, payload: object
    ) -> tuple[dict[str, object] | None, str | None, str | None]:
        """Resolve strict upstream references for the asynchronous task boundary."""
        if not isinstance(payload, Mapping):
            return None, "INTEGRATION_REQUEST_INVALID", "request must be an object"
        cv_id = payload.get("cv_id")
        position_id = payload.get("position_id")
        if not isinstance(cv_id, str) or not cv_id.strip():
            return None, "CV_REFERENCE_MISSING", "cv_id is required"
        if not isinstance(position_id, str) or not position_id.strip():
            return None, "POSITION_REFERENCE_MISSING", "position_id is required"
        try:
            cv_payload = self._cv_source.fetch_cv_profile(cv_id)
            target_type = payload.get("target_type", "standard_position")
            position_payload = (
                self._position_source.fetch_enterprise_job_profile(position_id)
                if target_type == "enterprise_job"
                else self._position_source.fetch_position_profile(position_id)
            )
        except UpstreamTimeoutError:
            return None, "UPSTREAM_TIMEOUT", "upstream contract request timed out"
        except UpstreamResponseError:
            return None, "UPSTREAM_HTTP_ERROR", "upstream contract request failed"
        except KeyError:
            return None, "UPSTREAM_CONTRACT_NOT_FOUND", "upstream contract was not found"
        except Exception:
            return None, "UPSTREAM_FETCH_FAILED", "upstream contract request failed"
        cv_mapping = map_authoritative_cv_profile(cv_payload)
        position_mapping = map_authoritative_position_profile(position_payload)
        if cv_mapping.value is None or position_mapping.value is None:
            return (
                None,
                "UPSTREAM_CONTRACT_INCOMPATIBLE",
                "one or more upstream contracts could not be mapped",
            )
        return (
            {
                "cv_profile": cv_mapping.value.model_dump(mode="json"),
                "position_profile": position_mapping.value.model_dump(mode="json"),
                "target_type": payload.get("target_type", "standard_position"),
                "use_enterprise_weights": payload.get("use_enterprise_weights", False),
                    "generate_learning_path": payload.get("generate_learning_path", True),
            },
            None,
            None,
        )

    @staticmethod
    def _rejected(code: str, message: str) -> ContractIntegrationResult:
        return ContractIntegrationResult(
            integration_status="rejected",
            error_code=code,
            error_message=message,
        )

    @staticmethod
    def _transport_rejection(code: str, message: str) -> ContractIntegrationResult:
        return ContractIntegrationResult(
            integration_status="rejected",
            contract_issues=(
                ContractIssue(
                    side="transport",
                    code=code,
                    path="$.upstream",
                    message=message,
                    severity="error",
                ),
            ),
            error_code=code,
            error_message=message,
        )
