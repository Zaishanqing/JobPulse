from pathlib import PurePath

from sqlalchemy.orm import Session, sessionmaker

from app.integrations.base import IntegrationError, IntegrationInputError
from app.integrations.contracts import DocumentParser, FileStorage, OCRProvider
from app.models.resume import Resume
from app.models.resume_parse_result import ResumeParseResult
from app.models.resume_skill import ResumeSkill
from app.models.source_cv import CVExtractionTask, SourceCVVersion
from app.models.candidate_decision import CandidateDecision
from app.models.candidate_submission import CandidateSubmission
from app.models.cv_position_classification import CVPositionClassification
from app.models.matching_service_reference import MatchingServiceReference
from app.infrastructure.tasks import SqlAlchemyTaskRepository
from app.infrastructure.outbox import SqlAlchemyOutboxRepository
from app.contexts.talent_acquisition import (
    ExtractionOutcome,
    ParseResultDraft,
    ParseResultRecord,
    ResumeDraft,
    ResumeRecord,
    ResumeSkillDraft,
    ResumeSkillRecord,
)
from app.contexts.talent_acquisition.ports import ResumeGrantRecord
from app.contexts.tasks import TaskRecord
from app.domain.json_types import freeze_json_object, thaw_json_object


def _json_object(value: object, field: str) -> dict[str, object]:
    return thaw_json_object(freeze_json_object(value, field=field))


class IntegrationResumeInputExtractor:
    def __init__(
        self,
        storage: FileStorage,
        document_parser: DocumentParser,
        ocr: OCRProvider,
    ) -> None:
        self._storage = storage
        self._document_parser = document_parser
        self._ocr = ocr

    def extract(
        self, storage_key: str, content_type: str | None, *, use_ocr: bool
    ) -> ExtractionOutcome:
        adapter = self._ocr if use_ocr else self._document_parser
        provider = adapter.status().provider
        content = self._storage.read(PurePath(storage_key).name)
        try:
            text = adapter.extract_text(
                content, content_type or "application/octet-stream"
            )
            if not text.strip():
                raise IntegrationInputError(
                    "ocr" if use_ocr else "document_parser",
                    provider,
                    "No text was extracted from the uploaded file",
                )
        except IntegrationError as exc:
            return ExtractionOutcome(
                "failed", "", provider, exc.__class__.__name__, str(exc)
            )
        return ExtractionOutcome("completed", text, provider)


class SqlAlchemyResumeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, draft: ResumeDraft) -> ResumeRecord:
        values = {
            "user_id": draft.user_id,
            "source_type": draft.source_type,
            "file_id": draft.file_id,
            "display_name": draft.display_name,
            "original_filename": draft.original_filename,
            "source_cv_version_id": draft.source_cv_version_id,
            "validated_cv_snapshot_id": draft.validated_cv_snapshot_id,
            "raw_text": draft.raw_text,
            "parse_status": draft.parse_status,
            "input_extraction_status": draft.input_extraction_status,
            "input_provider": draft.input_provider,
            "input_error_code": draft.input_error_code,
            "input_error_message": draft.input_error_message,
        }
        if draft.resume_id is not None:
            values["id"] = draft.resume_id
        row = Resume(**values)
        self._session.add(row)
        self._session.flush()
        return self._resume_record(row)

    def get(self, resume_id: str) -> ResumeRecord | None:
        row = self._session.get(Resume, resume_id)
        return self._resume_record(row) if row is not None else None

    def update_validated_source(
        self,
        resume_id: str,
        *,
        validated_cv_snapshot_id: str,
        source_cv_version_id: str,
        raw_text: str,
    ) -> ResumeRecord:
        row = self._session.get(Resume, resume_id)
        if row is None:
            raise LookupError(resume_id)
        row.source_cv_version_id = source_cv_version_id
        row.validated_cv_snapshot_id = validated_cv_snapshot_id
        row.raw_text = raw_text
        row.parse_status = "completed"
        row.input_extraction_status = "completed"
        row.input_provider = "cv_extraction_http"
        row.input_error_code = None
        row.input_error_message = None
        self._session.flush()
        return self._resume_record(row)

    def get_by_source_cv_version(
        self, source_cv_version_id: str
    ) -> ResumeRecord | None:
        version = self._session.get(SourceCVVersion, source_cv_version_id)
        if version is None:
            return None
        row = (
            self._session.query(Resume)
            .join(
                SourceCVVersion,
                Resume.source_cv_version_id == SourceCVVersion.id,
            )
            .filter(SourceCVVersion.source_cv_id == version.source_cv_id)
            .one_or_none()
        )
        return self._resume_record(row) if row is not None else None

    def list_by_user(self, user_id: str) -> list[ResumeRecord]:
        rows = (
            self._session.query(Resume)
            .filter(Resume.user_id == user_id)
            .order_by(Resume.created_at.desc())
            .all()
        )
        return [self._resume_record(row) for row in rows]

    def rename(self, resume_id: str, display_name: str) -> ResumeRecord:
        row = self._session.get(Resume, resume_id)
        if row is None:
            raise LookupError(resume_id)
        row.display_name = display_name
        self._session.flush()
        return self._resume_record(row)

    def delete(self, resume_id: str) -> None:
        # Keep immutable CV source/snapshot lineage while removing the user's
        # generated Resume projection.
        self._session.query(CVExtractionTask).filter(
            CVExtractionTask.resume_id == resume_id
        ).update({CVExtractionTask.resume_id: None}, synchronize_session=False)
        self._session.query(ResumeSkill).filter(
            ResumeSkill.resume_id == resume_id
        ).delete()
        self._session.query(ResumeParseResult).filter(
            ResumeParseResult.resume_id == resume_id
        ).delete()
        # 匹配报告引用与企业候选人记录属于这份简历的工作数据,随简历一起删除。
        self._session.query(MatchingServiceReference).filter(
            MatchingServiceReference.resume_id == resume_id
        ).delete()
        self._session.query(CandidateDecision).filter(
            CandidateDecision.resume_id == resume_id
        ).delete()
        self._session.query(CandidateSubmission).filter(
            CandidateSubmission.resume_id == resume_id
        ).delete()
        row = self._session.get(Resume, resume_id)
        if row is None:
            raise LookupError(resume_id)
        self._session.delete(row)

    def set_parse_status(self, resume_id: str, status: str) -> None:
        row = self._session.get(Resume, resume_id)
        if row is None:
            raise LookupError(resume_id)
        row.parse_status = status

    def get_parse_result(self, resume_id: str) -> ParseResultRecord | None:
        row = (
            self._session.query(ResumeParseResult)
            .filter(ResumeParseResult.resume_id == resume_id)
            .first()
        )
        return self._parse_record(row) if row is not None else None

    def save_parse_result(self, draft: ParseResultDraft) -> ParseResultRecord:
        row = (
            self._session.query(ResumeParseResult)
            .filter(ResumeParseResult.resume_id == draft.resume_id)
            .first()
        )
        values = {
            "education": [_json_object(item, "education") for item in draft.education],
            "projects": [_json_object(item, "projects") for item in draft.projects],
            "internships": [_json_object(item, "internships") for item in draft.internships],
            "skills": [_json_object(item, "skills") for item in draft.skills],
            "certificates": [_json_object(item, "certificates") for item in draft.certificates],
            "competitions": [_json_object(item, "competitions") for item in draft.competitions],
            "parse_confidence": draft.parse_confidence,
            "need_review": draft.need_review,
        }
        if row is None:
            row = ResumeParseResult(resume_id=draft.resume_id, **values)
            self._session.add(row)
        else:
            for name, value in values.items():
                setattr(row, name, value)
        self._session.flush()
        return self._parse_record(row)

    def replace_skills(
        self, resume_id: str, skills: tuple[ResumeSkillDraft, ...]
    ) -> list[ResumeSkillRecord]:
        self._session.query(ResumeSkill).filter(
            ResumeSkill.resume_id == resume_id
        ).delete()
        rows = [
            ResumeSkill(
                resume_id=resume_id,
                skill_id=item.skill_id,
                raw_skill=item.raw_skill,
                confidence=item.confidence,
                evidence=item.evidence,
                proficiency=item.proficiency,
            )
            for item in skills
        ]
        self._session.add_all(rows)
        self._session.flush()
        return [self._skill_record(row) for row in rows]

    def list_skills(self, resume_id: str) -> list[ResumeSkillRecord]:
        rows = (
            self._session.query(ResumeSkill)
            .filter(ResumeSkill.resume_id == resume_id)
            .order_by(ResumeSkill.created_at.asc())
            .all()
        )
        return [self._skill_record(row) for row in rows]

    def save_position_classifications(
        self,
        resume_id: str,
        *,
        normalized_payload,
        source_snapshot_id: str,
    ) -> None:
        projected: list[dict[str, object]] = []
        for item in normalized_payload.get("position_classifications", []):
            if not isinstance(item, dict):
                continue
            classification = item.get("job_classification")
            if not isinstance(classification, dict):
                continue
            projected.append(
                {
                    "source_object_id": item.get("source_object_id"),
                    "source_scope": item.get("source_scope"),
                    "raw_text": classification.get("source_title"),
                    "taxonomy_version": classification.get("taxonomy_version"),
                    **{
                        key: value
                        for key, value in classification.items()
                        if key not in {"schema_version", "taxonomy_version", "source_title"}
                    },
                    # The classifier contract currently carries evidence IDs,
                    # while the matching profile expects full Evidence objects.
                    # Keep the classification and expose no fabricated quote.
                    "feature_evidence_refs": [],
                }
            )
        values = {
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "classifications": projected,
            "source_run_ids": [source_snapshot_id],
        }
        row = self._session.get(CVPositionClassification, resume_id)
        if row is None:
            self._session.add(
                CVPositionClassification(resume_id=resume_id, **values)
            )
        else:
            for key, value in values.items():
                setattr(row, key, value)
        self._session.flush()

    @staticmethod
    def _resume_record(row: Resume) -> ResumeRecord:
        return ResumeRecord(
            row.id, row.user_id, row.source_type, row.file_id, row.raw_text,
            row.parse_status, row.input_extraction_status, row.input_provider,
            row.input_error_code, row.input_error_message,
            row.source_cv_version_id, row.validated_cv_snapshot_id,
            row.created_at, row.updated_at,
            row.display_name, row.original_filename,
        )

    @staticmethod
    def _parse_record(row: ResumeParseResult) -> ParseResultRecord:
        return ParseResultRecord(
            row.id,
            row.resume_id,
            tuple(row.education or []),
            tuple(row.projects or []),
            tuple(row.internships or []),
            tuple(row.skills or []),
            tuple(row.certificates or []),
            tuple(row.competitions or []),
            row.parse_confidence,
            row.need_review,
        )

    @staticmethod
    def _skill_record(row: ResumeSkill) -> ResumeSkillRecord:
        return ResumeSkillRecord(
            row.id, row.resume_id, row.skill_id, row.raw_skill,
            row.confidence, row.evidence, row.proficiency,
        )


class SqlAlchemyResumeUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyResumeUnitOfWork":
        self._session = self._session_factory()
        self.resumes = SqlAlchemyResumeRepository(self._session)
        self._tasks = SqlAlchemyTaskRepository(self._session)
        return self

    def add_task(self, record: TaskRecord) -> None:
        self._tasks.add(record)

    def add_outbox(self, draft) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        SqlAlchemyOutboxRepository(self._session).add(draft)

    def active_grants(self, resume_id: str) -> tuple[ResumeGrantRecord, ...]:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        rows = (
            self._session.query(CandidateSubmission)
            .filter(
                CandidateSubmission.resume_id == resume_id,
                CandidateSubmission.status == "submitted",
            )
            .order_by(CandidateSubmission.id)
            .all()
        )
        return tuple(
            ResumeGrantRecord(row.id, row.grant_version, row.enterprise_id)
            for row in rows
        )

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            self.rollback()
        self._session.close()

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
