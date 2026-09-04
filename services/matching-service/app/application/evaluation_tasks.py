"""Task orchestration, idempotency, retries, persistence and freshness checks."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.application.contract_mapping import (
    map_authoritative_cv_profile,
    map_authoritative_position_profile,
)
from app.application.evaluation import MatchEvaluationService
from app.application.learning_paths import LearningPathService
from app.domain.outbox import OutboxRecord, outbox_id_for_task
from app.domain.privacy import find_pii, redact_pii
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.queue import TaskQueueMessage, task_message_id
from app.domain.requirement_graph import is_specialty_route_graph
from app.domain.tasks import (
    POSITION_REQUIREMENT_GRAPH_NOT_APPLICABLE,
    AuditRecord,
    EvaluationQueryResult,
    EvaluationTask,
    PersistedEvaluation,
    PersistenceVersions,
    TaskQueryResult,
    TaskSubmissionResult,
)
from app.ports.repositories import UnitOfWorkFactory

DEFAULT_PERSISTENCE_VERSIONS = PersistenceVersions(
    profile_contract_mapping_version="contract-mapping.v3",
    graph_version="graph.disabled",
    embedding_model="embedding.disabled",
    embedding_version="embedding.disabled",
    embedding_dimension=0,
    vector_text_derivation_version="semantic-fragment.v1",
    semantic_algorithm_version="semantic-shadow.v1",
    semantic_threshold_version="semantic-shadow-disabled.v1",
    evaluation_algorithm_version="deterministic-matching.v9",
    scoring_algorithm_version="explainable-scoring.v4",
    scoring_config_version="scoring-config.v3",
    gap_algorithm_version="deterministic-gap-path.v3",
    gap_config_version="gap-analysis-config.v2",
)

# 运行时配置漂移导致的原因码；get_evaluation 会按当前服务版本重新计算，
# 一旦恢复一致就应清除，避免旧配置升级后所有历史报告被永久标记过期。
RUNTIME_STALE_REASONS = frozenset(
    {
        "EMBEDDING_REVISION_CHANGED",
        "SEMANTIC_INDEX_REVISION_CHANGED",
        "ALGORITHM_VERSION_CHANGED",
    }
)


class EvaluationTaskService:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        evaluation_service: MatchEvaluationService,
        learning_path_service: LearningPathService,
        *,
        versions: PersistenceVersions = DEFAULT_PERSISTENCE_VERSIONS,
        max_attempts: int = 3,
        clock: Callable[[], datetime] | None = None,
        before_success_commit: Callable[[], None] | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._evaluation_service = evaluation_service
        self._learning_path_service = learning_path_service
        self._versions = versions
        self._max_attempts = max_attempts
        self._clock = clock or (lambda: datetime.now(UTC))
        self._before_success_commit = before_success_commit

    def submit(
        self,
        payload: object,
        idempotency_key: str,
        access_scope: str,
        *,
        execute_immediately: bool = True,
        create_outbox: bool = False,
        tenant_ref: str | None = None,
    ) -> TaskSubmissionResult:
        if isinstance(payload, Mapping):
            schema_version = payload.get(
                "schema_version", "matching-evaluation-request.v1"
            )
            if schema_version != "matching-evaluation-request.v1":
                return self._submission_error(
                    "UNSUPPORTED_MATCHING_CONTRACT_VERSION",
                    "unsupported matching request schema_version",
                )
            for key, supported in (
                ("cv_profile", {"cv-match-profile.v1", "cv-matching-input-bundle.v1"}),
                (
                    "position_profile",
                    {"position-match-profile.v1", "position-matching-input-bundle.v1"},
                ),
            ):
                profile = payload.get(key)
                if isinstance(profile, Mapping):
                    declared_versions = {
                        profile[name]
                        for name in ("schema_version", "contract_version")
                        if name in profile
                    }
                    if not declared_versions or not declared_versions.issubset(supported):
                        return self._submission_error(
                            "UNSUPPORTED_MATCHING_CONTRACT_VERSION",
                            f"unsupported {key} schema_version",
                        )
            target_type = payload.get("target_type", "standard_position")
            if target_type not in {"standard_position", "enterprise_job"}:
                return self._submission_error(
                    "EVALUATION_TARGET_TYPE_INVALID",
                    "target_type is not supported by the frozen Contract",
                )
            for option in ("use_enterprise_weights", "generate_learning_path"):
                if option in payload and not isinstance(payload[option], bool):
                    return self._submission_error(
                        "EVALUATION_OPTION_INVALID", f"{option} must be boolean"
                    )
            if payload.get("use_enterprise_weights", False) and target_type != "enterprise_job":
                return self._submission_error(
                    "ENTERPRISE_WEIGHTS_TARGET_INVALID",
                    "enterprise weights require target_type=enterprise_job",
                )
            payload_tenant_ref = payload.get("tenant_ref")
            expected_tenant_ref = tenant_ref
            if expected_tenant_ref is None:
                scope_parts = access_scope.split(":")
                expected_tenant_ref = scope_parts[1] if len(scope_parts) >= 2 else None
            if (
                payload_tenant_ref is not None
                and expected_tenant_ref is not None
                and payload_tenant_ref != expected_tenant_ref
            ):
                return self._submission_error(
                    "TENANT_REF_MISMATCH",
                    "tenant_ref does not match the authenticated access scope",
                )
        if not idempotency_key.strip():
            return self._submission_error(
                "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required"
            )
        if not access_scope.strip():
            return self._submission_error(
                "ACCESS_SCOPE_REQUIRED", "X-Access-Scope is required"
            )
        profiles = self._parse_profiles(payload)
        if isinstance(profiles, TaskSubmissionResult):
            return profiles
        cv, position = profiles
        profile_payload = {
            "cv_profile": cv.model_dump(mode="python"),
            "position_profile": position.model_dump(mode="python"),
        }
        if find_pii(
            profile_payload,
            technical_context_allowed=bool(position.responsibility_requirements),
        ):
            return self._submission_error(
                "PROFILE_PII_FORBIDDEN", "profile payload contains prohibited PII"
            )
        target_type = (
            str(payload["target_type"])
            if isinstance(payload, Mapping) and isinstance(payload.get("target_type"), str)
            else "standard_position"
        )
        versions = self._versions.model_copy(
            update={
                "target_type": target_type,
                "use_enterprise_weights": bool(payload.get("use_enterprise_weights", False)),
                "generate_learning_path": bool(payload.get("generate_learning_path", True)),
                "cv_profile_version": cv.profile_version,
                "position_profile_version": position.profile_version,
                "cv_source_version": cv.source_version,
                "position_source_version": position.source_version,
                "cv_taxonomy_version": cv.taxonomy_version,
                "position_taxonomy_version": position.taxonomy_version,
                "position_graph_version": position.graph_version,
                "position_requirement_graph_version": (
                    position.requirement_graph.graph_version
                    if target_type == "standard_position"
                    and is_specialty_route_graph(position.requirement_graph)
                    else POSITION_REQUIREMENT_GRAPH_NOT_APPLICABLE
                ),
                "scoring_config_version": getattr(
                    self._evaluation_service,
                    "scoring_config_version",
                    lambda _: self._versions.scoring_config_version,
                )(bool(payload.get("use_enterprise_weights", False))),
            }
        )
        now = self._clock()
        task_id = "task_" + uuid4().hex

        with self._unit_of_work() as uow:
            previous = uow.tasks.find_by_idempotency_key(idempotency_key, access_scope)
            exact = next(
                (
                    item
                    for item in previous
                    if item.idempotency_key == idempotency_key
                    and item.versions == versions
                ),
                None,
            )
            if exact is not None:
                if create_outbox and exact.status != "succeeded":
                    self._ensure_outbox(uow, exact, now)
                uow.commit()
                existing = exact
            else:
                for item in previous:
                    self._mark_result_stale(
                        uow,
                        item,
                        "INPUT_IDENTITY_CHANGED"
                        if item.idempotency_key != idempotency_key
                        else "ALGORITHM_VERSION_CHANGED",
                        now,
                    )
                existing = EvaluationTask(
                    task_id=task_id,
                    access_scope=access_scope,
                    idempotency_key=idempotency_key,
                    versions=versions,
                    status="pending",
                    max_attempts=self._max_attempts,
                    created_at=now,
                    updated_at=now,
                    cv_profile=cv,
                    target_type=target_type,
                    position_profile=position,
                )
                uow.tasks.save(existing)
                uow.audits.append(self._audit(existing, "task_created", None, "pending", now))
                if create_outbox:
                    self._ensure_outbox(uow, existing, now)
                uow.commit()

        if exact is not None:
            if (
                execute_immediately
                and existing.status == "failed"
                and existing.attempt < existing.max_attempts
            ):
                existing = self.execute(existing.task_id, access_scope).task or existing
            return TaskSubmissionResult(task=existing, created=False)
        if not execute_immediately:
            return TaskSubmissionResult(task=existing, created=True)
        completed = self.execute(existing.task_id, access_scope).task or existing
        return TaskSubmissionResult(task=completed, created=True)

    @property
    def unit_of_work_factory(self) -> UnitOfWorkFactory:
        return self._unit_of_work

    @property
    def clock(self) -> Callable[[], datetime]:
        return self._clock

    @staticmethod
    def _ensure_outbox(uow: object, task: EvaluationTask, now: datetime) -> OutboxRecord:
        existing = uow.outbox.get_for_task(task.task_id, task.access_scope)
        if existing is not None:
            return existing
        message = TaskQueueMessage(
            message_id=task_message_id(task.task_id, task.versions.signature),
            task_id=task.task_id,
            access_scope=task.access_scope,
            version_signature=task.versions.signature,
            published_at=now,
        )
        record = OutboxRecord(
            outbox_id=outbox_id_for_task(task.task_id),
            access_scope=task.access_scope,
            task_id=task.task_id,
            message_id=message.message_id,
            payload=message,
            available_at=now,
            created_at=now,
            updated_at=now,
        )
        uow.outbox.save(record)
        return record

    def claim(
        self,
        task_id: str,
        access_scope: str,
        *,
        lease_owner: str,
        lease_seconds: float,
    ) -> TaskQueryResult:
        if not lease_owner or lease_seconds <= 0:
            raise ValueError("lease owner and positive lease duration are required")
        with self._unit_of_work() as uow:
            task = uow.tasks.get(task_id, access_scope)
            if task is None:
                uow.commit()
                return self._task_error("TASK_NOT_FOUND", "task was not found")
            now = self._clock()
            if (
                task.status == "running"
                and task.attempt >= task.max_attempts
                and (
                    task.lease_expires_at is None or task.lease_expires_at <= now
                )
            ):
                exhausted = task.model_copy(
                    update={
                        "status": "failed",
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "error_code": "TASK_ATTEMPTS_EXHAUSTED",
                        "error_message": "task exceeded max_attempts after lease expiry",
                        "updated_at": now,
                    }
                )
                uow.tasks.save(exhausted)
                uow.audits.append(
                    self._audit(
                        exhausted,
                        "task_failed",
                        "running",
                        "failed",
                        now,
                        reason="TASK_ATTEMPTS_EXHAUSTED",
                    )
                )
                uow.commit()
                return TaskQueryResult(task=exhausted)
            claimed = uow.tasks.claim(
                task_id,
                access_scope,
                lease_owner,
                now,
                now + timedelta(seconds=lease_seconds),
            )
            if claimed is None:
                current = uow.tasks.get(task_id, access_scope)
                uow.commit()
                return TaskQueryResult(task=current)
            if task.status in {"failed", "running"}:
                uow.audits.append(
                    self._audit(
                        claimed,
                        "task_retried",
                        task.status,
                        "pending",
                        now,
                        claimed.attempt,
                    )
                )
            uow.audits.append(
                self._audit(
                    claimed,
                    "task_started",
                    "pending" if task.status in {"failed", "running"} else task.status,
                    "running",
                    now,
                )
            )
            uow.commit()
        return TaskQueryResult(task=claimed)

    def execute(
        self, task_id: str, access_scope: str, *, recover_running: bool = False
    ) -> TaskQueryResult:
        owner = "inline-executor"
        claimed = self.claim(
            task_id,
            access_scope,
            lease_owner=owner,
            lease_seconds=300,
        )
        if claimed.task is None or claimed.task.lease_owner != owner:
            return claimed
        return self.execute_claimed(task_id, access_scope, lease_owner=owner)

    def execute_claimed(
        self, task_id: str, access_scope: str, *, lease_owner: str
    ) -> TaskQueryResult:
        with self._unit_of_work() as uow:
            running = uow.tasks.get(task_id, access_scope)
            if (
                running is None
                or running.status != "running"
                or running.lease_owner != lease_owner
            ):
                uow.commit()
                return self._task_error("TASK_LEASE_NOT_OWNED", "task lease is not owned")
            uow.commit()

        try:
            evaluation = self._evaluation_service.evaluate(
                {
                    "cv_profile": running.cv_profile.model_dump(mode="json"),
                    "position_profile": running.position_profile.model_dump(mode="json"),
                    "tenant_ref": running.tenant_ref,
                    "target_type": running.target_type,
                    "use_enterprise_weights": running.versions.use_enterprise_weights,
                }
            )
            if evaluation.evaluation_status != "completed":
                raise _TaskFailure(
                    evaluation.error_code or "MATCH_EVALUATION_REJECTED",
                    evaluation.error_message or "matching evaluation was rejected",
                )
            persisted_evaluation_id = "evaluation_" + running.task_id
            evaluation = evaluation.model_copy(
                update={"evaluation_id": persisted_evaluation_id}
            )
            final_result = evaluation.final_match_result
            if final_result is not None:
                evaluation = evaluation.model_copy(
                    update={
                        "final_match_result": final_result.model_copy(
                            update={
                                "source_evaluation_id": evaluation.evaluation_id
                            }
                        )
                    }
                )
            gap = self._learning_path_service.generate(
                {
                    "evaluation": evaluation.model_dump(mode="json"),
                    **(
                        {
                            "cv_profile": running.cv_profile.model_dump(mode="json"),
                            "position_profile": running.position_profile.model_dump(mode="json"),
                            "target_type": running.target_type,
                            "use_enterprise_weights": running.versions.use_enterprise_weights,
                        }
                        if running.versions.generate_learning_path
                        else {}
                    ),
                }
            )
            if gap.generation_status != "completed":
                raise _TaskFailure(
                    gap.error_code or "LEARNING_PATH_REJECTED",
                    gap.error_message or "learning path generation was rejected",
                )
            if not running.versions.generate_learning_path:
                gap = gap.model_copy(
                    update={
                        "generation_status": "rejected",
                        "learning_path": (),
                        "error_code": "LEARNING_PATH_NOT_REQUESTED",
                        "error_message": "learning path generation was not requested",
                    }
                )
            finished_at = self._clock()
            record = PersistedEvaluation(
                evaluation_id=evaluation.evaluation_id,
                task_id=running.task_id,
                idempotency_key=running.idempotency_key,
                access_scope=access_scope,
                versions=running.versions,
                evaluation=evaluation,
                gap_analysis=gap,
                created_at=finished_at,
                updated_at=finished_at,
            )
            succeeded = running.model_copy(
                update={
                    "status": "succeeded",
                    "evaluation_id": evaluation.evaluation_id,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "updated_at": finished_at,
                }
            )
            with self._unit_of_work() as uow:
                current = uow.tasks.get(running.task_id, access_scope)
                if (
                    current is None
                    or current.status != "running"
                    or current.lease_owner != lease_owner
                ):
                    uow.commit()
                    return self._task_error(
                        "TASK_LEASE_NOT_OWNED", "task lease is not owned"
                    )
                uow.evaluations.save(record)
                if self._before_success_commit is not None:
                    self._before_success_commit()
                uow.tasks.save(succeeded)
                uow.audits.append(
                    self._audit(
                        succeeded, "task_succeeded", "running", "succeeded", finished_at
                    )
                )
                uow.commit()
            return TaskQueryResult(task=succeeded)
        except Exception as exc:  # application boundary maps failures to stable task state
            if isinstance(exc, _TaskFailure):
                code, message = exc.code, exc.message
            else:
                code = "EVALUATION_TASK_EXECUTION_FAILED"
                raw_message = str(exc) or type(exc).__name__
                message = str(redact_pii(raw_message))
            failed_at = self._clock()
            failed = running.model_copy(
                update={
                    "status": "failed",
                    "error_code": code,
                    "error_message": message,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "updated_at": failed_at,
                }
            )
            with self._unit_of_work() as uow:
                current = uow.tasks.get(running.task_id, access_scope)
                if (
                    current is None
                    or current.status != "running"
                    or current.lease_owner != lease_owner
                ):
                    uow.commit()
                    return self._task_error(
                        "TASK_LEASE_NOT_OWNED", "task lease is not owned"
                    )
                uow.tasks.save(failed)
                uow.audits.append(
                    self._audit(
                        failed, "task_failed", "running", "failed", failed_at, reason=code
                    )
                )
                uow.commit()
            return TaskQueryResult(task=failed)

    def get_task(
        self, task_id: str, access_scope: str, *, service_wide: bool = False
    ) -> TaskQueryResult:
        with self._unit_of_work() as uow:
            task = (
                uow.tasks.get_any(task_id)
                if service_wide
                else uow.tasks.get(task_id, access_scope)
            )
            uow.commit()
        return TaskQueryResult(task=task) if task else self._task_error(
            "TASK_NOT_FOUND", "task was not found"
        )

    def abandon(self, task_id: str, access_scope: str) -> TaskQueryResult:
        """Move an unfinished task to a terminal state and revoke its lease.

        Clearing the lease prevents an already-running worker from committing a
        late result: ``execute_claimed`` verifies both status and lease ownership
        immediately before persistence. Failed tasks are returned idempotently so
        a repeated UI request cannot create another state transition.
        """
        with self._unit_of_work() as uow:
            task = uow.tasks.get(task_id, access_scope)
            if task is None:
                uow.commit()
                return self._task_error("TASK_NOT_FOUND", "task was not found")
            if task.status == "succeeded":
                uow.commit()
                return self._task_error(
                    "TASK_ALREADY_SUCCEEDED", "succeeded task cannot be abandoned"
                )
            if task.status == "failed":
                uow.commit()
                return TaskQueryResult(task=task)
            now = self._clock()
            abandoned = task.model_copy(
                update={
                    "status": "failed",
                    "error_code": "TASK_ABANDONED_BY_USER",
                    "error_message": "task was abandoned by the user before restart",
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "updated_at": now,
                }
            )
            uow.tasks.save(abandoned)
            uow.audits.append(
                self._audit(
                    abandoned,
                    "task_abandoned",
                    task.status,
                    "failed",
                    now,
                    reason="TASK_ABANDONED_BY_USER",
                )
            )
            uow.commit()
        return TaskQueryResult(task=abandoned)

    def validate_succeeded_duplicate(
        self, task_id: str, access_scope: str, version_signature: str
    ) -> TaskQueryResult:
        """Validate the durable task/result relationship before ACKing a duplicate."""
        with self._unit_of_work() as uow:
            task = uow.tasks.get(task_id, access_scope)
            if task is None:
                uow.commit()
                return self._task_error("TASK_NOT_FOUND", "task was not found")
            if task.versions.signature != version_signature:
                uow.commit()
                return self._task_error(
                    "TASK_MESSAGE_VERSION_MISMATCH",
                    "task message version does not match the persisted task",
                )
            if task.status != "succeeded":
                uow.commit()
                return self._task_error(
                    "TASK_NOT_SUCCEEDED", "task is not a succeeded terminal task"
                )
            result = (
                uow.evaluations.get(task.evaluation_id, access_scope)
                if task.evaluation_id is not None
                else None
            )
            valid_result = (
                result is not None
                and result.evaluation_id == task.evaluation_id
                and result.task_id == task.task_id
                and result.access_scope == task.access_scope
                and result.versions.signature == version_signature
                and result.idempotency_key == task.idempotency_key
            )
            uow.commit()
        if not valid_result:
            return self._task_error(
                "TASK_TERMINAL_EVALUATION_MISMATCH",
                "succeeded task does not match its persisted evaluation",
            )
        return TaskQueryResult(task=task)

    def get_evaluation(
        self, evaluation_id: str, access_scope: str, *, service_wide: bool = False
    ) -> EvaluationQueryResult:
        with self._unit_of_work() as uow:
            result = (
                uow.evaluations.get_any(evaluation_id)
                if service_wide
                else uow.evaluations.get(evaluation_id, access_scope)
            )
            if result is None:
                uow.commit()
                return EvaluationQueryResult(
                    error_code="EVALUATION_NOT_FOUND",
                    error_message="evaluation was not found",
                )
            stale_reasons = [
                reason
                for reason in result.stale_reason_codes
                if reason not in RUNTIME_STALE_REASONS
            ]
            current_versions = self._versions.model_copy(
                update={
                    "target_type": result.versions.target_type,
                    "use_enterprise_weights": result.versions.use_enterprise_weights,
                    "generate_learning_path": result.versions.generate_learning_path,
                    "cv_profile_version": result.versions.cv_profile_version,
                    "position_profile_version": result.versions.position_profile_version,
                    "cv_source_version": result.versions.cv_source_version,
                    "position_source_version": result.versions.position_source_version,
                    "cv_taxonomy_version": result.versions.cv_taxonomy_version,
                    "position_taxonomy_version": result.versions.position_taxonomy_version,
                    "position_graph_version": result.versions.position_graph_version,
                    "position_requirement_graph_version": result.versions.position_requirement_graph_version,
                    "scoring_config_version": getattr(
                        self._evaluation_service,
                        "scoring_config_version",
                        lambda _: self._versions.scoring_config_version,
                    )(result.versions.use_enterprise_weights),
                }
            )
            if (
                result.versions.embedding_version != current_versions.embedding_version
                and "EMBEDDING_REVISION_CHANGED" not in stale_reasons
            ):
                stale_reasons.append("EMBEDDING_REVISION_CHANGED")
            if (
                result.versions.semantic_index_revision
                != current_versions.semantic_index_revision
                and "SEMANTIC_INDEX_REVISION_CHANGED" not in stale_reasons
            ):
                stale_reasons.append("SEMANTIC_INDEX_REVISION_CHANGED")
            if (
                result.versions != current_versions
                and "ALGORITHM_VERSION_CHANGED" not in stale_reasons
            ):
                stale_reasons.append("ALGORITHM_VERSION_CHANGED")
            if (
                "ALGORITHM_VERSION_CHANGED" in result.stale_reason_codes
                and "ALGORITHM_VERSION_CHANGED" not in stale_reasons
            ):
                superseded = any(
                    item.task_id != result.task_id
                    and item.versions.signature != result.versions.signature
                    for item in uow.tasks.find_by_idempotency_key(
                        result.idempotency_key, access_scope
                    )
                )
                if superseded:
                    stale_reasons.append("ALGORITHM_VERSION_CHANGED")
            changed = tuple(stale_reasons) != result.stale_reason_codes
            if not stale_reasons and result.stale:
                now = self._clock()
                result = result.model_copy(
                    update={
                        "stale": False,
                        "stale_reason_codes": (),
                        "updated_at": now,
                    }
                )
                uow.evaluations.save(result)
                task = (
                    uow.tasks.get_any(result.task_id)
                    if service_wide
                    else uow.tasks.get(result.task_id, access_scope)
                )
                if task is not None:
                    uow.audits.append(
                        self._audit(
                            task,
                            "evaluation_current",
                            task.status,
                            task.status,
                            now,
                            reason="VERSIONS_RECONCILED",
                        )
                    )
            elif stale_reasons and (not result.stale or changed):
                now = self._clock()
                result = result.model_copy(
                    update={
                        "stale": True,
                        "stale_reason_codes": tuple(stale_reasons),
                        "updated_at": now,
                    }
                )
                uow.evaluations.save(result)
                task = (
                    uow.tasks.get_any(result.task_id)
                    if service_wide
                    else uow.tasks.get(result.task_id, access_scope)
                )
                if task is not None:
                    uow.audits.append(
                        self._audit(
                            task,
                            "evaluation_stale",
                            task.status,
                            task.status,
                            now,
                            reason=stale_reasons[-1],
                        )
                    )
            uow.commit()
        return EvaluationQueryResult(result=result)

    def audit_records(self, task_id: str, access_scope: str) -> tuple[AuditRecord, ...]:
        with self._unit_of_work() as uow:
            records = uow.audits.list_for_task(task_id, access_scope)
            uow.commit()
        return records

    def task_status_counts(self) -> dict[str, int]:
        with self._unit_of_work() as uow:
            counts = uow.tasks.count_by_status()
            uow.commit()
        return counts

    def _mark_result_stale(
        self, uow: object, task: EvaluationTask, reason: str, now: datetime
    ) -> None:
        if task.evaluation_id is None:
            return
        result = uow.evaluations.get(task.evaluation_id, task.access_scope)
        if result is None:
            return
        reasons = tuple(dict.fromkeys((*result.stale_reason_codes, reason)))
        uow.evaluations.save(
            result.model_copy(
                update={"stale": True, "stale_reason_codes": reasons, "updated_at": now}
            )
        )
        uow.audits.append(
            self._audit(
                task,
                "evaluation_stale",
                task.status,
                task.status,
                now,
                reason=reason,
            )
        )

    @staticmethod
    def _parse_profiles(
        payload: object,
    ) -> tuple[CVMatchProfile, PositionMatchProfile] | TaskSubmissionResult:
        if not isinstance(payload, Mapping):
            return EvaluationTaskService._submission_error(
                "EVALUATION_TASK_REQUEST_INVALID", "request must be an object"
            )
        cv_mapping = map_authoritative_cv_profile(payload.get("cv_profile"))
        position_mapping = map_authoritative_position_profile(
            payload.get("position_profile")
        )
        if cv_mapping.value is None or position_mapping.value is None:
            message = "; ".join(
                issue.message
                for issue in (*cv_mapping.issues, *position_mapping.issues)
            )
            return EvaluationTaskService._submission_error(
                "EVALUATION_TASK_INPUT_INVALID",
                message or "matching input profiles could not be mapped",
            )
        cv = cv_mapping.value
        position = position_mapping.value
        if cv.profile_version is None or position.profile_version is None:
            return EvaluationTaskService._submission_error(
                "EVALUATION_TASK_PROFILE_VERSION_MISSING",
                "both profile versions are required",
            )
        return cv, position

    @staticmethod
    def _submission_error(code: str, message: str) -> TaskSubmissionResult:
        return TaskSubmissionResult(
            created=False, error_code=code, error_message=message
        )

    @staticmethod
    def _task_error(code: str, message: str) -> TaskQueryResult:
        return TaskQueryResult(error_code=code, error_message=message)

    def _audit(
        self,
        task: EvaluationTask,
        event_type: str,
        from_status: str | None,
        to_status: str | None,
        now: datetime,
        attempt: int | None = None,
        reason: str | None = None,
    ) -> AuditRecord:
        return AuditRecord(
            audit_id="audit_" + uuid4().hex,
            task_id=task.task_id,
            access_scope=task.access_scope.replace("@", "_at_"),
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            attempt=attempt if attempt is not None else task.attempt,
            reason_code=reason,
            idempotency_key=task.idempotency_key,
            algorithm_version=task.versions.signature,
            occurred_at=now,
        )


class _TaskFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
