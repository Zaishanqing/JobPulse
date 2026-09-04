from app.domain.json_types import FrozenJsonObject
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from app.integration_events import OutboxMessageDraft

from app.contexts.tasks import TaskRecord


JsonObject = FrozenJsonObject
JsonSections = tuple[JsonObject, ...]


@dataclass(frozen=True)
class ResumeRecord:
    resume_id: str
    user_id: str
    source_type: str
    file_id: str | None
    raw_text: str
    parse_status: str
    input_extraction_status: str
    input_provider: str | None
    input_error_code: str | None
    input_error_message: str | None
    source_cv_version_id: str | None
    validated_cv_snapshot_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    display_name: str | None = None
    original_filename: str | None = None


@dataclass(frozen=True)
class ResumeDraft:
    user_id: str
    source_type: str
    file_id: str | None
    raw_text: str
    parse_status: str
    input_extraction_status: str
    input_provider: str | None
    input_error_code: str | None = None
    input_error_message: str | None = None
    source_cv_version_id: str | None = None
    validated_cv_snapshot_id: str | None = None
    display_name: str | None = None
    original_filename: str | None = None
    resume_id: str | None = None


@dataclass(frozen=True)
class ParseResultRecord:
    parse_result_id: str
    resume_id: str
    education: JsonSections
    projects: JsonSections
    internships: JsonSections
    skills: JsonSections
    certificates: JsonSections
    competitions: JsonSections
    parse_confidence: float
    need_review: bool


@dataclass(frozen=True)
class ParseResultDraft:
    resume_id: str
    education: JsonSections
    projects: JsonSections
    internships: JsonSections
    skills: JsonSections
    certificates: JsonSections
    competitions: JsonSections
    parse_confidence: float
    need_review: bool


@dataclass(frozen=True)
class ParseResultChanges:
    changed_fields: frozenset[str]
    education: JsonSections | None = None
    projects: JsonSections | None = None
    internships: JsonSections | None = None
    skills: JsonSections | None = None
    certificates: JsonSections | None = None
    competitions: JsonSections | None = None
    parse_confidence: float | None = None
    need_review: bool | None = None


@dataclass(frozen=True)
class ResumeSkillRecord:
    resume_skill_id: str
    resume_id: str
    skill_id: str
    raw_skill: str
    confidence: float
    evidence: str | None
    proficiency: str | None


@dataclass(frozen=True)
class ResumeSkillDraft:
    skill_id: str
    raw_skill: str
    confidence: float
    evidence: str | None
    proficiency: str | None


@dataclass(frozen=True)
class ExtractionOutcome:
    status: str
    text: str
    provider: str
    error_code: str | None = None
    error_message: str | None = None


class ResumeInputExtractionPort(Protocol):
    def extract(self, storage_key: str, content_type: str | None, *, use_ocr: bool) -> ExtractionOutcome: ...


@dataclass(frozen=True)
class ResumeGrantRecord:
    grant_id: str
    grant_version: int
    enterprise_id: str


class ResumeRepository(Protocol):
    def add(self, draft: ResumeDraft) -> ResumeRecord: ...
    def get(self, resume_id: str) -> ResumeRecord | None: ...
    def get_by_source_cv_version(self, source_cv_version_id: str) -> ResumeRecord | None: ...
    def update_validated_source(
        self,
        resume_id: str,
        *,
        validated_cv_snapshot_id: str,
        source_cv_version_id: str,
        raw_text: str,
    ) -> ResumeRecord: ...
    def list_by_user(self, user_id: str) -> list[ResumeRecord]: ...
    def rename(self, resume_id: str, display_name: str) -> ResumeRecord: ...
    def delete(self, resume_id: str) -> None: ...
    def set_parse_status(self, resume_id: str, status: str) -> None: ...
    def get_parse_result(self, resume_id: str) -> ParseResultRecord | None: ...
    def save_parse_result(self, draft: ParseResultDraft) -> ParseResultRecord: ...
    def replace_skills(self, resume_id: str, skills: tuple[ResumeSkillDraft, ...]) -> list[ResumeSkillRecord]: ...
    def list_skills(self, resume_id: str) -> list[ResumeSkillRecord]: ...
    def save_position_classifications(
        self,
        resume_id: str,
        *,
        normalized_payload: FrozenJsonObject,
        source_snapshot_id: str,
    ) -> None: ...


class ResumeUnitOfWork(Protocol):
    resumes: ResumeRepository
    def add_task(self, record: TaskRecord) -> None: ...
    def add_outbox(self, draft: OutboxMessageDraft) -> None: ...
    def active_grants(self, resume_id: str) -> tuple[ResumeGrantRecord, ...]: ...
    def __enter__(self) -> "ResumeUnitOfWork": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
