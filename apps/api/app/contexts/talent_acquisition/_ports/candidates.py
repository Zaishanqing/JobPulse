from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.candidates import WeightedSkill
from app.contexts.tasks import TaskRecord
from app.integration_events import OutboxMessageDraft


@dataclass(frozen=True)
class CandidateJobProfile:
    job_id: str
    enterprise_id: str
    enterprise_owner_id: str
    weights: tuple[WeightedSkill, ...]
    status: str = "published"


@dataclass(frozen=True)
class CandidateResumeProfile:
    resume_id: str
    owner_id: str
    skill_ids: frozenset[str]
    display_name: str = ""
    parse_status: str = ""
    validated_cv_snapshot_id: str | None = None


@dataclass(frozen=True)
class CandidateSubmissionRecord:
    submission_id: str
    resume_id: str
    job_id: str
    enterprise_id: str
    resume_owner_id: str
    status: str
    created_at: datetime | None
    updated_at: datetime | None
    grant_version: int = 1
    display_name: str = ""
    parse_status: str = ""
    validated_cv_snapshot_id: str | None = None
    skill_count: int = 0


@dataclass(frozen=True)
class CandidateApplicationOption:
    resume_id: str
    display_name: str
    validated_cv_snapshot_id: str | None
    eligible: bool
    eligibility_reason: str
    submission: CandidateSubmissionRecord | None


@dataclass(frozen=True)
class CandidateDecisionRecord:
    decision_id: str
    job_id: str
    resume_id: str
    decision: str
    decided_by: str
    evaluation_id: str | None = None
    task_id: str | None = None
    algorithm_version: str | None = None
    reason_code: str | None = None
    reason_text: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CandidateJobProfilePort(Protocol):
    def get(self, job_id: str) -> CandidateJobProfile | None: ...


class CandidateResumeProfilePort(Protocol):
    def get(self, resume_id: str) -> CandidateResumeProfile | None: ...
    def list_for_owner(self, owner_id: str) -> list[CandidateResumeProfile]: ...


class CandidateRepository(Protocol):
    def get_submission(self, job_id: str, resume_id: str) -> CandidateSubmissionRecord | None: ...
    def get_submission_by_id(
        self, job_id: str, submission_id: str
    ) -> CandidateSubmissionRecord | None: ...
    def list_submissions(self, job_id: str) -> list[CandidateSubmissionRecord]: ...
    def save_submission(
        self, job: CandidateJobProfile, resume: CandidateResumeProfile, status: str
    ) -> CandidateSubmissionRecord: ...
    def is_submitted(self, job: CandidateJobProfile, resume: CandidateResumeProfile) -> bool: ...
    def list_decisions(self, job_id: str) -> list[CandidateDecisionRecord]: ...
    def save_decision(
        self,
        job_id: str,
        resume_id: str,
        decision: str,
        actor_id: str,
        evaluation_id: str | None = None,
        task_id: str | None = None,
        algorithm_version: str | None = None,
        reason_code: str | None = None,
        reason_text: str | None = None,
    ) -> CandidateDecisionRecord: ...


class CandidateUnitOfWork(Protocol):
    candidates: CandidateRepository

    def add_task(self, record: TaskRecord) -> None: ...
    def add_outbox(self, draft: OutboxMessageDraft) -> None: ...
    def __enter__(self) -> "CandidateUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
