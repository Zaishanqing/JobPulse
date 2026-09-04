from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Callable
from uuid import uuid4

from app.domain.accounts import AccountActor, ENTERPRISE_READ_ROLES
from app.domain.candidates import (
    CandidateRuleViolation,
    INTERNAL_CANDIDATE_ROLES,
    WeightedSkill,  # noqa: F401 - compatibility re-export for the application facade
    require_decision,
    require_submission_actor,
)
from app.contexts.talent_acquisition._ports.candidates import (
    CandidateApplicationOption,
    CandidateDecisionRecord,
    CandidateJobProfile,
    CandidateJobProfilePort,
    CandidateResumeProfile,
    CandidateResumeProfilePort,
    CandidateSubmissionRecord,
    CandidateUnitOfWork,
)
from app.contexts.matching_learning import (
    MatchingContractUnavailable,
    MatchingIdentityPort,
    MatchingServicePort,
    MatchingServiceReferenceRecord,
    MatchingUnitOfWork,
    RemoteEvaluation,
    MatchingServiceError,
)
from app.contexts.matching_learning import MatchingContractReader
from app.contexts.tasks import TaskWorkflowPort
from app.domain.errors import PermissionDenied
from app.profile_index_events import (
    enterprise_projection_entity_id,
    personal_tenant_ref,
    profile_index_event,
    tenant_ref,
)


class CandidateJobNotFound(LookupError):
    pass


class CandidateResumeNotFound(LookupError):
    pass


class CandidateSubmissionNotFound(LookupError):
    pass


class EnterpriseMatchNotFound(LookupError):
    pass


class CandidateConflict(RuntimeError):
    pass


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PerResumeMatchItem:
    submission_id: str
    resume_id: str
    status: str  # "created" | "reconciling" | "rejected" | "error"
    task_id: str | None = None
    evaluation_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class CandidateMatchTask:
    job_id: str
    items: tuple[PerResumeMatchItem, ...]


@dataclass(frozen=True)
class CandidateBoardItem:
    submission_id: str
    resume_id: str
    candidate_display_name: str
    candidate_status: str  # "submitted" | "revoked"
    evaluation_id: str | None
    # never_matched | pending | running | failed | succeeded | stale | needs_rematch | revoked
    evaluation_status: str
    task_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    decision: CandidateDecisionRecord | None = None
    evaluation: RemoteEvaluation | None = None
    evaluation_reference: MatchingServiceReferenceRecord | None = None
    previous_evaluation: RemoteEvaluation | None = None
    previous_evaluation_reference: MatchingServiceReferenceRecord | None = None


@dataclass(frozen=True)
class CandidateDecisionBoard:
    enterprise_job_id: str
    items: tuple[CandidateBoardItem, ...]


@dataclass(frozen=True)
class DecisionAuditMetric:
    numerator: int
    denominator: int

    @property
    def rate(self) -> float | None:
        if self.denominator == 0:
            return None
        return round(self.numerator / self.denominator, 4)


@dataclass(frozen=True)
class DecisionAuditCase:
    evaluation: RemoteEvaluation
    reference: MatchingServiceReferenceRecord
    submission: CandidateSubmissionRecord | None
    decisions: tuple[CandidateDecisionRecord, ...]
    selected_decision: CandidateDecisionRecord | None
    formal_direction: str | None
    classifications: tuple[str, ...]
    critical_gap_count: int
    version_consistent: bool
    historical: bool


@dataclass(frozen=True)
class RecruiterDecisionAudit:
    enterprise_job_id: str
    config_version: str
    cases: tuple[DecisionAuditCase, ...]
    overall_agreement: DecisionAuditMetric
    high_score_rejection: DecisionAuditMetric
    low_score_acceptance: DecisionAuditMetric
    critical_gap_disagreement: DecisionAuditMetric
    reason_code_distribution: tuple[tuple[str, int], ...]
    evaluation_count: int
    paired_decision_count: int
    missing_decision_count: int
    missing_reason_count: int
    version_mismatch_count: int
    duplicate_decision_count: int
    unavailable_evaluation_count: int


@dataclass(frozen=True)
class ManageCandidates:
    uow_factory: Callable[[], CandidateUnitOfWork]
    jobs: CandidateJobProfilePort
    resumes: CandidateResumeProfilePort
    tasks: TaskWorkflowPort
    matching_service: MatchingServicePort
    matching_identities: MatchingIdentityPort
    contracts: MatchingContractReader
    matching_uow_factory: Callable[[], MatchingUnitOfWork]
    vector_index_enabled: bool = True

    def submit(
        self, actor: AccountActor, job_id: str, resume_id: str
    ) -> CandidateSubmissionRecord:
        try:
            require_submission_actor(actor.role)
        except CandidateRuleViolation as exc:
            raise PermissionDenied("Only a personal user can submit a resume") from exc
        job = self._job(job_id)
        if job.status != "published":
            raise CandidateJobNotFound("Published enterprise job not found")
        resume = self._resume(resume_id)
        if resume.owner_id != actor.account_id:
            raise PermissionDenied("Cannot submit another user's resume")
        if not resume.validated_cv_snapshot_id:
            raise CandidateRuleViolation(
                "Resume requires a confirmed ValidatedCVSnapshot before submission"
            )
        if self.contracts.cv_profile(
            resume.resume_id, snapshot_id=resume.validated_cv_snapshot_id
        ) is None:
            raise CandidateRuleViolation(
                "Resume snapshot is not confirmed or is not matchable"
            )
        with self.uow_factory() as uow:
            record = uow.candidates.save_submission(job, resume, "submitted")
            enterprise_tenant = tenant_ref(job.enterprise_id)
            if self.vector_index_enabled:
                uow.add_outbox(
                    profile_index_event(
                        vector_event_type="cv_profile_published",
                        entity_type="cv",
                        entity_id=enterprise_projection_entity_id(
                            resume.resume_id, record.submission_id
                        ),
                        source_entity_id=resume.resume_id,
                        tenant=enterprise_tenant,
                        target_type="candidate_cv",
                        grant_id=record.submission_id,
                        grant_version=record.grant_version,
                        personal_tenant=personal_tenant_ref(resume.owner_id),
                        enterprise_tenant=enterprise_tenant,
                        snapshot_id=resume.validated_cv_snapshot_id,
                        enterprise_job_id=job.job_id,
                    )
                )
            uow.commit()
            return record

    def application_options(
        self, actor: AccountActor, job_id: str
    ) -> list[CandidateApplicationOption]:
        try:
            require_submission_actor(actor.role)
        except CandidateRuleViolation as exc:
            raise PermissionDenied("Only a personal user can view application options") from exc
        job = self._job(job_id)
        if job.status != "published":
            raise CandidateJobNotFound("Published enterprise job not found")
        options: list[CandidateApplicationOption] = []
        for resume in self.resumes.list_for_owner(actor.account_id):
            if not resume.validated_cv_snapshot_id:
                eligible = False
                reason = "validated_cv_snapshot_missing"
            elif self.contracts.cv_profile(
                resume.resume_id, snapshot_id=resume.validated_cv_snapshot_id
            ) is None:
                eligible = False
                reason = "validated_cv_snapshot_not_matchable"
            else:
                eligible = True
                reason = "eligible"
            with self.uow_factory() as uow:
                submission = uow.candidates.get_submission(job_id, resume.resume_id)
            options.append(
                CandidateApplicationOption(
                    resume.resume_id,
                    resume.display_name or resume.resume_id,
                    resume.validated_cv_snapshot_id,
                    eligible,
                    reason,
                    submission,
                )
            )
        return options

    def revoke(
        self, actor: AccountActor, job_id: str, resume_id: str
    ) -> CandidateSubmissionRecord:
        self._job(job_id)
        with self.uow_factory() as uow:
            current = uow.candidates.get_submission(job_id, resume_id)
            if (
                actor.role != "personal_user"
                or current is None
                or current.resume_owner_id != actor.account_id
            ):
                raise CandidateSubmissionNotFound("Candidate submission not found")
            job = self._job(job_id)
            resume = self._resume(resume_id)
            record = uow.candidates.save_submission(job, resume, "revoked")
            enterprise_tenant = tenant_ref(job.enterprise_id)
            if self.vector_index_enabled:
                uow.add_outbox(
                    profile_index_event(
                        vector_event_type="cv_profile_revoked",
                        entity_type="cv",
                        entity_id=enterprise_projection_entity_id(
                            resume.resume_id, record.submission_id
                        ),
                        source_entity_id=resume.resume_id,
                        tenant=enterprise_tenant,
                        target_type="candidate_cv",
                        grant_id=record.submission_id,
                        grant_version=record.grant_version,
                        personal_tenant=personal_tenant_ref(resume.owner_id),
                        enterprise_tenant=enterprise_tenant,
                        snapshot_id=resume.validated_cv_snapshot_id,
                        enterprise_job_id=job.job_id,
                    )
                )
            uow.commit()
            return record

    def candidate_submissions(
        self, actor: AccountActor, job_id: str
    ) -> list[CandidateSubmissionRecord]:
        job = self._job(job_id)
        self._authorize_job(actor, job, write=False)
        with self.uow_factory() as uow:
            return uow.candidates.list_submissions(job_id)

    def match(
        self, actor: AccountActor, job_id: str, submission_ids: list[str]
    ) -> CandidateMatchTask:
        unique_ids = list(dict.fromkeys(submission_ids))
        if not unique_ids:
            raise CandidateRuleViolation("submission_ids must contain at least one submission")
        job = self._job(job_id)
        self._authorize_job(actor, job, write=False)
        identity = self.matching_identities.resolve(actor)
        position_profile = self.contracts.enterprise_job_profile(job_id)
        if position_profile is None:
            raise CandidateRuleViolation("Enterprise job has no published KG position profile")
        correlation_id = f"enterprise-match-{uuid4()}"
        logger.info(
            "enterprise_candidate_match_started",
            extra={
                "correlation_id": correlation_id,
                "actor_id": actor.account_id,
                "enterprise_id": job.enterprise_id,
                "job_id": job.job_id,
                "submission_count": len(unique_ids),
            },
        )
        items: list[PerResumeMatchItem] = []
        for submission_id in unique_ids:
            with self.uow_factory() as uow:
                submission = uow.candidates.get_submission_by_id(job_id, submission_id)
            if submission is None:
                items.append(
                    PerResumeMatchItem(
                        submission_id=submission_id,
                        resume_id="",
                        status="error",
                        error_code="CANDIDATE_SUBMISSION_NOT_FOUND",
                        error_message="Candidate submission not found",
                    )
                )
                continue
            if submission.status != "submitted":
                items.append(
                    PerResumeMatchItem(
                        submission_id=submission.submission_id,
                        resume_id=submission.resume_id,
                        status="rejected",
                        error_code="CANDIDATE_SUBMISSION_INACTIVE",
                        error_message="Candidate submission is not active",
                    )
                )
                continue
            try:
                item = self._match_one(
                    actor,
                    identity,
                    job,
                    submission,
                    position_profile,
                    correlation_id,
                )
            except CandidateResumeNotFound as exc:
                items.append(
                    PerResumeMatchItem(
                        submission_id=submission.submission_id,
                        resume_id=submission.resume_id,
                        status="error",
                        error_code=type(exc).__name__,
                        error_message=str(exc),
                    )
                )
            except PermissionDenied as exc:
                items.append(
                    PerResumeMatchItem(
                        submission_id=submission.submission_id,
                        resume_id=submission.resume_id,
                        status="rejected",
                        error_code="CANDIDATE_ACCESS_DENIED",
                        error_message=str(exc),
                    )
                )
            else:
                items.append(item)
        logger.info(
            "enterprise_candidate_match_finished",
            extra={
                "correlation_id": correlation_id,
                "actor_id": actor.account_id,
                "enterprise_id": job.enterprise_id,
                "job_id": job.job_id,
                "success_count": sum(item.status == "created" for item in items),
                "failure_count": sum(item.status != "created" for item in items),
            },
        )
        return CandidateMatchTask(job_id=job_id, items=tuple(items))

    def _match_one(
        self,
        actor: AccountActor,
        identity,
        job: CandidateJobProfile,
        submission: CandidateSubmissionRecord,
        position_profile: dict[str, object],
        correlation_id: str,
    ) -> PerResumeMatchItem:
        resume_id = submission.resume_id
        resume = self._resume(resume_id)
        with self.uow_factory() as uow:
            self._authorize_resume(uow, actor, job, resume)
        cv_profile = self.contracts.cv_profile(
            resume_id, snapshot_id=submission.validated_cv_snapshot_id
        )
        if cv_profile is None:
            return PerResumeMatchItem(
                submission_id=submission.submission_id,
                resume_id=resume_id,
                status="rejected",
                error_code="CV_PROFILE_NOT_FOUND",
                error_message="resume has no validated CV snapshot",
            )
        if not resume.skill_ids:
            return PerResumeMatchItem(
                submission_id=submission.submission_id,
                resume_id=resume_id,
                status="rejected",
                error_code="RESUME_SKILLS_EMPTY",
                error_message="resume skill profile is empty",
            )
        target_ref = position_profile["position_id"]
        cv_profile_version = self._profile_identity_version(cv_profile)
        position_profile_version = self._profile_identity_version(position_profile)
        graph_version = str(position_profile.get("graph_version", "legacy-unspecified"))
        raw_key = (
            f"enterprise-job:{job.job_id}:{resume_id}:{target_ref}:"
            f"{cv_profile_version}:{position_profile_version}"
        )
        # matching-service persists idempotency_key in a 200-char column and
        # rejects longer values with 500. Keep the enterprise-batch key compact
        # and deterministic so remote_unknown retries reuse the same task.
        idempotency_key = (
            f"enterprise-job:{job.job_id}:{resume_id}:"
            f"{hashlib.sha256(raw_key.encode('utf-8')).hexdigest()}"
        )
        with self.matching_uow_factory() as uow:
            uow.matching.save_intent(
                idempotency_key=idempotency_key,
                user_id=actor.account_id,
                tenant_id=identity.tenant_id,
                resume_id=resume_id,
                position_id=target_ref,
                target_type="enterprise_job",
                cv_profile_version=cv_profile_version,
                position_profile_version=position_profile_version,
                status="intended",
                access_scope=identity.access_scope,
                source_version=(
                    f"cv={cv_profile.get('source_version', 'legacy-unspecified')}|"
                    f"position={position_profile.get('source_version', 'legacy-unspecified')}"
                ),
                taxonomy_version=(
                    f"cv={cv_profile.get('taxonomy_version', 'legacy-unspecified')}|"
                    f"position={position_profile.get('taxonomy_version', 'legacy-unspecified')}"
                ),
                graph_version=graph_version,
            )
            uow.commit()

        try:
            task = self.matching_service.create_task(
                identity,
                cv_id=resume_id,
                # 授权契约按企业岗位主键查询；画像内部仍保留
                # enterprise_job:<id> 作为正式 PositionProfile 身份。
                position_id=job.job_id,
                target_type="enterprise_job",
                use_enterprise_weights=True,
                generate_learning_path=True,
                cv_profile=cv_profile,
                position_profile=position_profile,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
        except MatchingServiceError as exc:
            error_code = exc.code
            status_code = exc.status_code
            if 400 <= status_code < 500 and status_code not in (429, 502, 503, 504):
                new_status = "rejected"
            else:
                new_status = "remote_unknown"
            with self.matching_uow_factory() as uow:
                uow.matching.update_intent_status(
                    idempotency_key, new_status, error_code=str(error_code)
                )
                uow.commit()
            logger.warning(
                "enterprise_candidate_match_service_failed",
                extra={
                    "correlation_id": correlation_id,
                    "actor_id": actor.account_id,
                    "enterprise_id": job.enterprise_id,
                    "job_id": job.job_id,
                    "submission_id": submission.submission_id,
                    "error_code": error_code,
                },
            )
            return PerResumeMatchItem(
                submission_id=submission.submission_id,
                resume_id=resume_id,
                status=new_status if new_status == "rejected" else "reconciling",
                error_code=str(error_code),
                error_message=str(exc),
            )

        with self.matching_uow_factory() as uow:
            uow.matching.upsert_service_reference(
                MatchingServiceReferenceRecord(
                    task_id=task.task_id,
                    evaluation_id=task.evaluation_id,
                    user_id=actor.account_id,
                    tenant_id=identity.tenant_id,
                    resume_id=resume_id,
                    position_id=target_ref,
                    provider="matching-service",
                    target_type="enterprise_job",
                    status=task.status,
                    idempotency_key=idempotency_key,
                    created_at=None,
                    updated_at=None,
                    access_scope=identity.access_scope,
                    source_version=str(
                        task.raw.get("source_version", "legacy-unspecified")
                    ),
                    cv_profile_version=cv_profile_version,
                    position_profile_version=position_profile_version,
                    taxonomy_version=str(
                        task.raw.get("taxonomy_version", "legacy-unspecified")
                    ),
                    graph_version=graph_version,
                    algorithm_version=str(
                        task.raw.get("algorithm_version", "legacy-unspecified")
                    ),
                )
            )
            uow.matching.update_intent_status(idempotency_key, "reference_saved")
            uow.commit()

        return PerResumeMatchItem(
            submission_id=submission.submission_id,
            resume_id=resume_id,
            status="created",
            task_id=task.task_id,
            evaluation_id=task.evaluation_id,
        )

    def list_reports(
        self, actor: AccountActor, job_id: str
    ) -> list[MatchingServiceReferenceRecord]:
        job = self._job(job_id)
        self._authorize_job(actor, job, write=False)
        with self.matching_uow_factory() as uow:
            return uow.matching.list_service_references(
                None if actor.role in INTERNAL_CANDIDATE_ROLES else actor.account_id,
                position_id=f"enterprise_job:{job_id}",
                target_type="enterprise_job",
            )

    def get_report(
        self, actor: AccountActor, job_id: str, evaluation_id: str
    ) -> RemoteEvaluation:
        job = self._job(job_id)
        self._authorize_job(actor, job, write=False)
        with self.matching_uow_factory() as uow:
            ref = uow.matching.get_service_reference(evaluation_id)
        if (
            ref is None
            or ref.position_id != f"enterprise_job:{job_id}"
            or ref.target_type != "enterprise_job"
            or (
                actor.role not in INTERNAL_CANDIDATE_ROLES
                and ref.user_id != actor.account_id
            )
        ):
            raise EnterpriseMatchNotFound("Enterprise match report not found")
        identity = self.matching_identities.resolve(actor)
        correlation_id = f"enterprise-match-report-{uuid4()}"
        logger.info(
            "enterprise_match_report_requested",
            extra={
                "correlation_id": correlation_id,
                "actor_id": actor.account_id,
                "enterprise_id": job.enterprise_id,
                "job_id": job.job_id,
                "evaluation_id": evaluation_id,
            },
        )
        result = self.matching_service.get_evaluation(
            identity, evaluation_id, correlation_id=correlation_id
        )
        resume = self.resumes.get(ref.resume_id)
        return replace(
            result,
            user_id=ref.user_id,
            resume_id=ref.resume_id,
            validated_cv_snapshot_id=(
                resume.validated_cv_snapshot_id if resume else None
            ),
            position_id=ref.position_id,
            provider=ref.provider,
            target_type=ref.target_type,
            use_enterprise_weights=True,
        )

    def decision_board(
        self, actor: AccountActor, job_id: str
    ) -> CandidateDecisionBoard:
        """One-shot aggregation of every candidate decision summary for a job.

        This is intentionally a competition-scale, read-only aggregation: it
        reads the latest formal evaluation and, when present, the immediately
        preceding compatible formal evaluation. Scores are never recomputed here.
        """
        job = self._job(job_id)
        self._authorize_job(actor, job, write=False)
        identity = self.matching_identities.resolve(actor)
        with self.uow_factory() as uow:
            submissions = uow.candidates.list_submissions(job_id)
            decisions = {
                record.resume_id: record
                for record in uow.candidates.list_decisions(job_id)
            }
        with self.matching_uow_factory() as uow:
            references = uow.matching.list_service_references(
                None if actor.role in INTERNAL_CANDIDATE_ROLES else actor.account_id,
                position_id=f"enterprise_job:{job_id}",
                target_type="enterprise_job",
                include_orphan_intents=True,
            )
        position_profile_error: tuple[str, str] | None = None
        try:
            position_profile = self.contracts.enterprise_job_profile(job_id)
        except MatchingContractUnavailable as exc:
            position_profile = None
            position_profile_error = ("KG_UNAVAILABLE", str(exc))
        by_resume: dict[str, list[MatchingServiceReferenceRecord]] = {}
        for reference in references:
            by_resume.setdefault(reference.resume_id, []).append(reference)

        correlation_id = f"enterprise-decision-board-{uuid4()}"
        logger.info(
            "enterprise_decision_board_requested",
            extra={
                "correlation_id": correlation_id,
                "actor_id": actor.account_id,
                "enterprise_id": job.enterprise_id,
                "job_id": job.job_id,
                "submission_count": len(submissions),
            },
        )
        items: list[CandidateBoardItem] = []
        for submission in submissions:
            refs = by_resume.get(submission.resume_id, [])
            ordered = sorted(
                refs,
                key=lambda ref: (
                    ref.created_at or datetime.min.replace(tzinfo=timezone.utc),
                    ref.evaluation_id or "",
                    ref.task_id,
                ),
                reverse=True,
            )
            latest = ordered[0] if ordered else None
            fetched: RemoteEvaluation | None = None
            evaluation_error: tuple[str, str] | None = None
            evaluation: RemoteEvaluation | None = None
            latest_status = (latest.status or "").lower() if latest else ""
            if (
                latest is not None
                and latest.evaluation_id
                and latest_status in {"succeeded", "completed", "current", "stale"}
            ):
                fetched, evaluation_error = self._board_evaluation(
                    identity, latest.evaluation_id, correlation_id
                )
            current_resume = self._resume(submission.resume_id)
            current_cv_profile = self.contracts.cv_profile(submission.resume_id)
            compatibility_status = self._evaluation_compatibility_status(
                latest,
                fetched,
                current_resume=current_resume,
                current_cv_profile=current_cv_profile,
                current_position_profile=position_profile,
                expected_position_id=f"enterprise_job:{job_id}",
            )
            evaluation_status = self._board_evaluation_status(
                submission.status,
                latest,
                fetched,
                compatibility_status,
            )
            task_error: tuple[str | None, str | None] | None = None
            if (
                evaluation_status == "failed"
                and fetched is None
                and latest is not None
                and latest.task_id
            ):
                task_error = self._board_task_error(identity, latest.task_id, correlation_id)
            error_code, error_message = self._board_error(
                evaluation_status,
                latest,
                fetched,
                evaluation_error=evaluation_error,
                task_error=task_error,
                position_profile_error=position_profile_error,
                current_cv_profile=current_cv_profile,
                current_position_profile=position_profile,
            )
            if evaluation_status == "succeeded":
                # Only a current, compatible, completed remote evaluation may
                # contribute formal score data.
                if fetched is not None:
                    evaluation = fetched
            previous_evaluation: RemoteEvaluation | None = None
            previous_reference: MatchingServiceReferenceRecord | None = None
            if evaluation is not None:
                for candidate_reference in ordered[1:]:
                    if (
                        not candidate_reference.evaluation_id
                        or (candidate_reference.status or "").lower()
                        not in {"succeeded", "completed", "current"}
                    ):
                        continue
                    candidate_evaluation, _ = self._board_evaluation(
                        identity, candidate_reference.evaluation_id, correlation_id
                    )
                    if candidate_evaluation is None:
                        continue
                    if (
                        candidate_evaluation.evaluation.get("evaluation_status")
                        != "completed"
                        or self._evaluation_compatibility_status(
                            candidate_reference,
                            candidate_evaluation,
                            current_resume=current_resume,
                            current_cv_profile=current_cv_profile,
                            current_position_profile=position_profile,
                            expected_position_id=f"enterprise_job:{job_id}",
                        )
                        is not None
                    ):
                        continue
                    previous_evaluation = candidate_evaluation
                    previous_reference = candidate_reference
                    break
            decision = decisions.get(submission.resume_id)
            if (
                evaluation_status != "succeeded"
                or latest is None
                or decision is None
                or decision.evaluation_id != latest.evaluation_id
            ):
                decision = None
            items.append(
                CandidateBoardItem(
                    submission_id=submission.submission_id,
                    resume_id=submission.resume_id,
                    candidate_display_name=submission.display_name,
                    candidate_status=submission.status,
                    evaluation_id=(
                        evaluation.evaluation_id
                        if evaluation is not None
                        else (latest.evaluation_id if latest else None)
                    ),
                    evaluation_status=evaluation_status,
                    task_id=(latest.task_id or None) if latest else None,
                    error_code=error_code,
                    error_message=error_message,
                    decision=decision,
                    evaluation=evaluation,
                    evaluation_reference=latest if evaluation is not None else None,
                    previous_evaluation=previous_evaluation,
                    previous_evaluation_reference=previous_reference,
                )
            )
        return CandidateDecisionBoard(enterprise_job_id=job_id, items=tuple(items))

    def decision_audit(
        self, actor: AccountActor, job_id: str
    ) -> RecruiterDecisionAudit:
        """Compare persisted recruiter decisions with formal evaluations, read-only.

        The audit consumes the formal recommendation and score. It never derives a
        replacement matching score or mutates an evaluation/decision.
        """
        job = self._job(job_id)
        self._authorize_job(actor, job, write=False)
        identity = self.matching_identities.resolve(actor)
        with self.uow_factory() as uow:
            submissions = {
                item.resume_id: item for item in uow.candidates.list_submissions(job_id)
            }
            raw_decisions = uow.candidates.list_decisions(job_id)
        with self.matching_uow_factory() as uow:
            raw_references = uow.matching.list_service_references(
                None if actor.role in INTERNAL_CANDIDATE_ROLES else actor.account_id,
                position_id=f"enterprise_job:{job_id}",
                target_type="enterprise_job",
                include_orphan_intents=False,
            )

        decisions_by_evaluation: dict[str, list[CandidateDecisionRecord]] = {}
        for decision in raw_decisions:
            if decision.evaluation_id:
                decisions_by_evaluation.setdefault(decision.evaluation_id, []).append(decision)
        references_by_evaluation: dict[str, MatchingServiceReferenceRecord] = {}
        for reference in sorted(raw_references, key=self._audit_reference_order):
            if reference.evaluation_id:
                references_by_evaluation[reference.evaluation_id] = reference
        latest_by_resume: dict[str, str] = {}
        for evaluation_id, reference in sorted(
            references_by_evaluation.items(),
            key=lambda item: self._audit_reference_order(item[1]),
            reverse=True,
        ):
            latest_by_resume.setdefault(reference.resume_id, evaluation_id)

        cases: list[DecisionAuditCase] = []
        unavailable = 0
        correlation_id = f"enterprise-decision-audit-{uuid4()}"
        for evaluation_id, reference in sorted(references_by_evaluation.items()):
            if (reference.status or "").lower() not in {
                "succeeded", "completed", "current", "stale"
            }:
                continue
            evaluation, _ = self._board_evaluation(
                identity, evaluation_id, correlation_id
            )
            if evaluation is None:
                unavailable += 1
                continue
            decisions = tuple(
                sorted(
                    decisions_by_evaluation.get(evaluation_id, []),
                    key=self._audit_decision_order,
                    reverse=True,
                )
            )
            selected = decisions[0] if decisions else None
            final = evaluation.evaluation.get("final_match_result") or {}
            recommendation = str(final.get("recommendation_level") or "")
            formal_direction = self._audit_formal_direction(recommendation)
            critical_gap_count = sum(
                1
                for gap in evaluation.gap_analysis.get("prioritized_gaps", [])
                if isinstance(gap, dict) and gap.get("priority") == "critical"
            )
            version_consistent = self._audit_version_consistent(
                selected, reference, evaluation
            )
            classifications = self._audit_classifications(
                selected,
                formal_direction=formal_direction,
                recommendation=recommendation,
                critical_gap_count=critical_gap_count,
                version_consistent=version_consistent,
            )
            cases.append(
                DecisionAuditCase(
                    evaluation=evaluation,
                    reference=reference,
                    submission=submissions.get(reference.resume_id),
                    decisions=decisions,
                    selected_decision=selected,
                    formal_direction=formal_direction,
                    classifications=classifications,
                    critical_gap_count=critical_gap_count,
                    version_consistent=version_consistent,
                    historical=latest_by_resume.get(reference.resume_id) != evaluation_id,
                )
            )

        comparable = [
            case
            for case in cases
            if case.selected_decision is not None
            and case.version_consistent
            and case.formal_direction is not None
        ]
        high = [case for case in comparable if case.formal_direction == "fit_high"]
        low = [case for case in comparable if case.formal_direction == "unfit_low"]
        critical = [case for case in comparable if case.critical_gap_count > 0]
        reason_counts: dict[str, int] = {}
        for case in cases:
            decision = case.selected_decision
            if decision is None:
                continue
            code = (decision.reason_code or "").strip() or "__missing__"
            reason_counts[code] = reason_counts.get(code, 0) + 1
        return RecruiterDecisionAudit(
            enterprise_job_id=job_id,
            config_version="enterprise-decision-audit.v1",
            cases=tuple(cases),
            overall_agreement=DecisionAuditMetric(
                sum(
                    case.selected_decision is not None
                    and (
                        case.selected_decision.decision == "fit"
                        and case.formal_direction in {"fit_high", "fit"}
                        or case.selected_decision.decision == "unfit"
                        and case.formal_direction == "unfit_low"
                    )
                    for case in comparable
                ),
                len(comparable),
            ),
            high_score_rejection=DecisionAuditMetric(
                sum(case.selected_decision.decision == "unfit" for case in high),
                len(high),
            ),
            low_score_acceptance=DecisionAuditMetric(
                sum(case.selected_decision.decision == "fit" for case in low),
                len(low),
            ),
            critical_gap_disagreement=DecisionAuditMetric(
                sum(case.selected_decision.decision == "fit" for case in critical),
                len(critical),
            ),
            reason_code_distribution=tuple(sorted(reason_counts.items())),
            evaluation_count=len(cases),
            paired_decision_count=sum(case.selected_decision is not None for case in cases),
            missing_decision_count=sum(case.selected_decision is None for case in cases),
            missing_reason_count=sum(
                case.selected_decision is not None
                and not (case.selected_decision.reason_code or "").strip()
                for case in cases
            ),
            version_mismatch_count=sum(
                case.selected_decision is not None and not case.version_consistent
                for case in cases
            ),
            duplicate_decision_count=sum(max(0, len(case.decisions) - 1) for case in cases),
            unavailable_evaluation_count=unavailable,
        )

    @staticmethod
    def _audit_reference_order(reference: MatchingServiceReferenceRecord) -> tuple:
        return (
            reference.updated_at or reference.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reference.task_id,
        )

    @staticmethod
    def _audit_decision_order(decision: CandidateDecisionRecord) -> tuple:
        return (
            decision.updated_at
            or decision.created_at
            or datetime.min.replace(tzinfo=timezone.utc),
            decision.decision_id,
        )

    @staticmethod
    def _audit_formal_direction(recommendation: str) -> str | None:
        # These are formal Matching recommendation labels, not D-side score thresholds.
        if recommendation == "strong_match":
            return "fit_high"
        if recommendation == "potential_match":
            return "fit"
        if recommendation in {"weak_match", "not_recommended"}:
            return "unfit_low"
        return None

    @staticmethod
    def _audit_version_consistent(
        decision: CandidateDecisionRecord | None,
        reference: MatchingServiceReferenceRecord,
        evaluation: RemoteEvaluation,
    ) -> bool:
        if decision is None:
            return True
        if decision.evaluation_id != evaluation.evaluation_id:
            return False
        if decision.task_id and decision.task_id not in {
            reference.task_id,
            evaluation.task_id,
        }:
            return False
        evaluation_algorithm = str(
            evaluation.evaluation.get("algorithm_version")
            or (evaluation.evaluation.get("final_match_result") or {}).get("algorithm_version")
            or ""
        )
        identities = [
            value
            for value in (decision.algorithm_version, evaluation_algorithm)
            if value and value != "legacy-unspecified"
        ]
        if len(set(identities)) > 1:
            return False
        if (
            reference.algorithm_version
            and reference.algorithm_version != "legacy-unspecified"
            and evaluation_algorithm
            and reference.algorithm_version != evaluation_algorithm
        ):
            return False
        return True

    @staticmethod
    def _audit_classifications(
        decision: CandidateDecisionRecord | None,
        *,
        formal_direction: str | None,
        recommendation: str,
        critical_gap_count: int,
        version_consistent: bool,
    ) -> tuple[str, ...]:
        if decision is None:
            return ("no_recruiter_decision",)
        labels: list[str] = []
        if not version_consistent:
            labels.append("version_mismatch")
        elif formal_direction is None:
            labels.append("not_comparable")
        else:
            agrees = (
                decision.decision == "fit" and formal_direction in {"fit_high", "fit"}
            ) or (decision.decision == "unfit" and formal_direction == "unfit_low")
            labels.append("agreement" if agrees else "disagreement")
            if recommendation == "strong_match" and decision.decision == "unfit":
                labels.append("high_score_rejection")
            if formal_direction == "unfit_low" and decision.decision == "fit":
                labels.append("low_score_acceptance")
            if critical_gap_count and decision.decision == "fit":
                labels.append("critical_gap_disagreement")
        if not (decision.reason_code or "").strip():
            labels.append("missing_reason")
        return tuple(labels)

    def _board_evaluation(
        self,
        identity,
        evaluation_id: str,
        correlation_id: str,
    ) -> tuple[RemoteEvaluation | None, tuple[str, str] | None]:
        try:
            return (
                self.matching_service.get_evaluation(
                    identity, evaluation_id, correlation_id=correlation_id
                ),
                None,
            )
        except MatchingServiceError as exc:
            logger.warning(
                "enterprise_decision_board_evaluation_unavailable",
                extra={
                    "correlation_id": correlation_id,
                    "evaluation_id": evaluation_id,
                    "error_code": exc.code,
                },
            )
            return None, (exc.code, str(exc))

    def _board_task_error(
        self,
        identity,
        task_id: str,
        correlation_id: str,
    ) -> tuple[str | None, str | None]:
        try:
            task = self.matching_service.get_task(
                identity, task_id, correlation_id=correlation_id
            )
            return task.error_code, task.error_message
        except MatchingServiceError as exc:
            logger.warning(
                "enterprise_decision_board_task_unavailable",
                extra={
                    "correlation_id": correlation_id,
                    "task_id": task_id,
                    "error_code": exc.code,
                },
            )
            return exc.code, str(exc)

    @classmethod
    def _board_error(
        cls,
        evaluation_status: str,
        reference: MatchingServiceReferenceRecord | None,
        evaluation: RemoteEvaluation | None,
        *,
        evaluation_error: tuple[str, str] | None,
        task_error: tuple[str | None, str | None] | None,
        position_profile_error: tuple[str, str] | None,
        current_cv_profile: dict[str, object] | None,
        current_position_profile: dict[str, object] | None,
    ) -> tuple[str | None, str | None]:
        if evaluation_status not in {"failed", "stale", "needs_rematch"}:
            return None, None
        remote_code = None
        remote_message = None
        if evaluation is not None:
            remote_code = evaluation.evaluation.get("error_code")
            remote_message = evaluation.evaluation.get("error_message")
        raw_code = str(
            remote_code
            or (task_error[0] if task_error else "")
            or (reference.error_code if reference else "")
            or (evaluation_error[0] if evaluation_error else "")
            or ""
        )
        raw_message = str(
            remote_message
            or (task_error[1] if task_error else "")
            or (reference.error_message if reference else "")
            or (evaluation_error[1] if evaluation_error else "")
            or ""
        )
        if position_profile_error is not None:
            raw_code, raw_message = position_profile_error
        elif evaluation_status == "needs_rematch" and current_cv_profile is None:
            raw_code = raw_code or "CV_STALE"
        elif evaluation_status == "needs_rematch" and current_position_profile is None:
            raw_code = raw_code or "POSITION_PROFILE_MISSING"
        elif (
            evaluation_status == "needs_rematch"
            and reference is not None
            and current_cv_profile is not None
            and reference.cv_profile_version
            and reference.cv_profile_version
            != cls._profile_identity_version(current_cv_profile)
        ):
            raw_code = raw_code or "CV_STALE"
        elif evaluation_status in {"stale", "needs_rematch"} and not raw_code:
            raw_code = "CONTRACT_INCOMPATIBLE"
        elif evaluation_status == "failed" and not raw_code:
            raw_code = "REMOTE_REJECTED"
        stable_code = cls._stable_board_error_code(raw_code)
        messages = {
            "CV_STALE": "候选人的当前简历画像已变化，请重新匹配。",
            "POSITION_PROFILE_MISSING": "当前岗位画像不可用，请先完成岗位画像发布。",
            "KG_UNAVAILABLE": "知识图谱暂时不可用，请稍后重试。",
            "MATCHING_TIMEOUT": "匹配服务响应超时，请稍后重试。",
            "CONTRACT_INCOMPATIBLE": "当前画像与该评估的输入版本不兼容，请重新匹配。",
            "REMOTE_REJECTED": "匹配服务未能完成该评估，请检查输入后重试。",
        }
        return stable_code, raw_message or messages[stable_code]

    @staticmethod
    def _stable_board_error_code(raw_code: str) -> str:
        code = raw_code.strip().upper()
        if "TIMEOUT" in code:
            return "MATCHING_TIMEOUT"
        if "CV" in code and any(
            marker in code for marker in {"STALE", "PROFILE_NOT_FOUND", "SNAPSHOT"}
        ):
            return "CV_STALE"
        if "POSITION" in code and any(
            marker in code for marker in {"MISSING", "NOT_FOUND", "PROFILE"}
        ):
            return "POSITION_PROFILE_MISSING"
        if "KG" in code or "KNOWLEDGE_GRAPH" in code:
            return "KG_UNAVAILABLE"
        if any(
            marker in code
            for marker in {"CONTRACT", "SCHEMA", "RESPONSE_INVALID", "INPUT_VERSION"}
        ):
            return "CONTRACT_INCOMPATIBLE"
        return "REMOTE_REJECTED"

    @staticmethod
    def _board_evaluation_status(
        submission_status: str,
        latest: MatchingServiceReferenceRecord | None,
        fetched: RemoteEvaluation | None,
        compatibility_status: str | None,
    ) -> str:
        if submission_status != "submitted":
            return "revoked"
        latest_status = (latest.status or "").lower() if latest is not None else ""
        # A missing remote payload must not turn a terminal reference status
        # into ``needs_rematch`` merely because its current profiles cannot be
        # compared.  Compatibility checks apply to fetched/current results;
        # reference-only statuses are normalized below.
        if compatibility_status is not None and (
            fetched is not None
            or latest_status in {"succeeded", "completed", "current"}
        ):
            return compatibility_status
        if fetched is not None:
            remote_status = (fetched.evaluation.get("evaluation_status") or "").lower()
            if remote_status == "completed":
                return "succeeded"
            if remote_status in {"failed", "rejected", "cancelled", "error"}:
                return "failed"
            if remote_status in {"pending", "queued", "created"}:
                return "pending"
            if remote_status in {"running", "processing"}:
                return "running"
            if remote_status == "stale":
                return "stale"
            if remote_status == "needs_rematch":
                return "needs_rematch"
            return "failed"
        if latest is None:
            return "never_matched"
        status = latest_status
        if status in {"pending", "queued", "created", "intended"}:
            return "pending"
        if status in {"running", "processing", "reconciling", "remote_unknown"}:
            return "running"
        if status in {
            "failed",
            "cancelled",
            "rejected",
            "error",
            "rejected_remote",
        }:
            return "failed"
        # A succeeded/current reference without a readable completed report is
        # not rankable and must not masquerade as a succeeded evaluation.
        if status in {"succeeded", "completed", "current"}:
            return "failed"
        if status == "stale":
            return "stale"
        if status == "needs_rematch":
            return "needs_rematch"
        return "failed"

    @staticmethod
    def _profile_identity_version(profile: dict[str, object]) -> str:
        """Stable input identity for board compatibility, independent of scoring."""
        identity = {key: value for key, value in profile.items() if key != "created_at"}
        payload = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    @classmethod
    def _evaluation_compatibility_status(
        cls,
        reference: MatchingServiceReferenceRecord | None,
        evaluation: RemoteEvaluation | None,
        *,
        current_resume: CandidateResumeProfile,
        current_cv_profile: dict[str, object] | None,
        current_position_profile: dict[str, object] | None,
        expected_position_id: str,
    ) -> str | None:
        if reference is None:
            return None
        if (reference.status or "").lower() == "stale" or (
            evaluation is not None and evaluation.stale
        ):
            return "stale"
        if current_cv_profile is None or current_position_profile is None:
            return "needs_rematch"
        current_snapshot_id = str(
            current_cv_profile.get("verification_snapshot_id")
            or current_cv_profile.get("validated_cv_snapshot_id")
            or ""
        )
        if (
            current_resume.validated_cv_snapshot_id
            and current_snapshot_id != current_resume.validated_cv_snapshot_id
        ):
            return "needs_rematch"
        current_cv_version = cls._profile_identity_version(current_cv_profile)
        current_position_version = cls._profile_identity_version(current_position_profile)
        if reference.cv_profile_version and reference.cv_profile_version != current_cv_version:
            return "needs_rematch"
        if (
            reference.position_profile_version
            and reference.position_profile_version != current_position_version
        ):
            return "needs_rematch"
        if evaluation is not None:
            if evaluation.resume_id and evaluation.resume_id != reference.resume_id:
                return "needs_rematch"
            expected_position_ids = {expected_position_id}
            if expected_position_id.startswith("enterprise_job:"):
                expected_position_ids.add(expected_position_id.split(":", 1)[1])
            if evaluation.position_id and evaluation.position_id not in expected_position_ids:
                return "needs_rematch"
            if (
                evaluation.validated_cv_snapshot_id
                and evaluation.validated_cv_snapshot_id != current_snapshot_id
            ):
                return "needs_rematch"
            remote_algorithm = str(evaluation.evaluation.get("algorithm_version") or "")
            if (
                reference.algorithm_version not in {"", "legacy-unspecified"}
                and remote_algorithm
                and reference.algorithm_version != remote_algorithm
            ):
                return "needs_rematch"
        return None

    def decide(
        self, actor: AccountActor, job_id: str, resume_id: str, decision: str,
        evaluation_id: str | None = None,
        reason_code: str | None = None,
        reason_text: str | None = None,
    ) -> CandidateDecisionRecord:
        require_decision(decision)
        reason_code = reason_code.strip() if reason_code else None
        reason_text = reason_text.strip() if reason_text else None
        if reason_code is not None and len(reason_code) > 64:
            raise CandidateRuleViolation("reason_code must be at most 64 characters")
        if reason_text is not None and len(reason_text) > 2000:
            raise CandidateRuleViolation("reason_text must be at most 2000 characters")
        job = self._job(job_id)
        self._authorize_job(actor, job, write=True)
        resume = self._resume(resume_id)
        with self.uow_factory() as uow:
            self._authorize_resume(uow, actor, job, resume)

        if evaluation_id is None:
            raise CandidateConflict(
                "evaluation_id is required for enterprise job candidate decisions"
            )

        with self.matching_uow_factory() as uow:
            reference = uow.matching.get_service_reference(evaluation_id)
        if reference is None:
            raise CandidateConflict("evaluation not found for this enterprise job")
        if reference.resume_id != resume_id:
            raise CandidateConflict("evaluation does not belong to this resume")
        if reference.position_id != f"enterprise_job:{job_id}":
            raise CandidateConflict("evaluation does not belong to this enterprise job")
        if reference.target_type != "enterprise_job":
            raise CandidateConflict("evaluation is not from enterprise job matching")

        with self.matching_uow_factory() as uow:
            references = uow.matching.list_service_references(
                None if actor.role in INTERNAL_CANDIDATE_ROLES else actor.account_id,
                position_id=f"enterprise_job:{job_id}",
                target_type="enterprise_job",
            )
        latest = next(
            iter(
                sorted(
                    (item for item in references if item.resume_id == resume_id),
                    key=lambda item: (
                        item.created_at or datetime.min.replace(tzinfo=timezone.utc),
                        item.evaluation_id or "",
                        item.task_id,
                    ),
                    reverse=True,
                )
            ),
            None,
        )
        if latest is None or latest.evaluation_id != evaluation_id:
            raise CandidateConflict("evaluation is not the latest candidate evaluation")
        if (latest.status or "").lower() not in {
            "created",
            "succeeded",
            "completed",
            "current",
        }:
            raise CandidateConflict("latest evaluation is not succeeded")

        identity = self.matching_identities.resolve(actor)
        correlation_id = f"enterprise-match-decision-{uuid4()}"
        logger.info(
            "enterprise_candidate_decision_requested",
            extra={
                "correlation_id": correlation_id,
                "actor_id": actor.account_id,
                "enterprise_id": job.enterprise_id,
                "job_id": job.job_id,
                "evaluation_id": evaluation_id,
            },
        )
        try:
            evaluation = self.matching_service.get_evaluation(
                identity, evaluation_id, correlation_id=correlation_id
            )
        except MatchingServiceError as exc:
            logger.warning(
                "enterprise_candidate_decision_service_failed",
                extra={
                    "correlation_id": correlation_id,
                    "actor_id": actor.account_id,
                    "enterprise_id": job.enterprise_id,
                    "job_id": job.job_id,
                    "evaluation_id": evaluation_id,
                    "error_code": exc.code,
                },
            )
            raise CandidateConflict("matching service unavailable for decision") from exc
        if evaluation.stale:
            raise CandidateConflict("evaluation is stale, job requirements may have changed")
        if evaluation.resume_id and evaluation.resume_id != resume_id:
            raise CandidateConflict("remote evaluation does not belong to this resume")
        expected_position_ids = {f"enterprise_job:{job_id}", job_id}
        if evaluation.position_id and evaluation.position_id not in expected_position_ids:
            raise CandidateConflict("remote evaluation does not belong to this enterprise job")
        eval_status = evaluation.evaluation.get("evaluation_status", "")
        if eval_status != "completed":
            raise CandidateConflict("evaluation is not succeeded")

        with self.uow_factory() as uow:
            submission = uow.candidates.get_submission(job_id, resume_id)
        if submission is None or submission.status != "submitted":
            raise CandidateConflict("candidate submission has been revoked")
        cv_profile = self.contracts.cv_profile(resume_id)
        if cv_profile is None:
            raise CandidateConflict("CV profile not available for verification")
        try:
            current_position_profile = self.contracts.enterprise_job_profile(job_id)
        except MatchingContractUnavailable as exc:
            raise CandidateConflict(
                "position profile unavailable for decision verification"
            ) from exc
        compatibility_status = self._evaluation_compatibility_status(
            reference,
            evaluation,
            current_resume=resume,
            current_cv_profile=cv_profile,
            current_position_profile=current_position_profile,
            expected_position_id=f"enterprise_job:{job_id}",
        )
        if compatibility_status is not None:
            raise CandidateConflict(
                "evaluation is stale or incompatible; candidate must be rematched"
            )
        with self.uow_factory() as uow:
            if not uow.candidates.is_submitted(job, resume):
                raise CandidateConflict("candidate submission has been revoked")
            record = uow.candidates.save_decision(
                job_id, resume_id, decision, actor.account_id,
                evaluation_id=evaluation_id,
                task_id=reference.task_id,
                algorithm_version=evaluation.evaluation.get("algorithm_version", ""),
                reason_code=reason_code,
                reason_text=reason_text,
            )
            uow.commit()
            return record

    def _job(self, job_id: str) -> CandidateJobProfile:
        job = self.jobs.get(job_id)
        if job is None:
            raise CandidateJobNotFound("Enterprise job not found")
        return job

    def _resume(self, resume_id: str) -> CandidateResumeProfile:
        resume = self.resumes.get(resume_id)
        if resume is None:
            raise CandidateResumeNotFound("Resume not found")
        return resume

    @staticmethod
    def _authorize_job(
        actor: AccountActor, job: CandidateJobProfile, *, write: bool
    ) -> None:
        if job.enterprise_owner_id == actor.account_id:
            return
        if not write and actor.role in ENTERPRISE_READ_ROLES:
            return
        raise PermissionDenied("No permission for this enterprise")

    @staticmethod
    def _authorize_resume(
        uow: CandidateUnitOfWork,
        actor: AccountActor,
        job: CandidateJobProfile,
        resume: CandidateResumeProfile,
    ) -> None:
        if actor.role in INTERNAL_CANDIDATE_ROLES:
            return
        if not uow.candidates.is_submitted(job, resume):
            raise PermissionDenied(
                "Resume is not submitted or authorized for this enterprise job"
            )
