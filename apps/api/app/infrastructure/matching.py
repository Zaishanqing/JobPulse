from datetime import datetime as _datetime, timedelta as _timedelta, timezone as _tz

from sqlalchemy.orm import Session, sessionmaker

from app.contexts.matching_learning.ports import LearningPathRecordData
from app.contexts.matching_learning.matching_service import MatchingServiceReferenceRecord
from app.contexts.tasks import TaskRecord
from app.infrastructure.tasks import SqlAlchemyTaskRepository
from app.models.matching_service_reference import MatchingServiceReference
from app.models.learning_path_record import LearningPathRecord
from app.models.matching_submission_intent import MatchingSubmissionIntent


class SqlAlchemyMatchingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_service_reference(
        self, record: MatchingServiceReferenceRecord
    ) -> MatchingServiceReferenceRecord:
        row = (
            self._session.query(MatchingServiceReference)
            .filter(MatchingServiceReference.task_id == record.task_id)
            .first()
        )
        if row is None and record.idempotency_key:
            row = (
                self._session.query(MatchingServiceReference)
                .filter(
                    MatchingServiceReference.user_id == record.user_id,
                    MatchingServiceReference.idempotency_key
                    == record.idempotency_key,
                )
                .first()
            )
        if row is None:
            row = MatchingServiceReference(
                task_id=record.task_id,
                evaluation_id=record.evaluation_id,
                user_id=record.user_id,
                tenant_id=record.tenant_id,
                resume_id=record.resume_id,
                position_id=record.position_id,
                provider=record.provider,
                target_type=record.target_type,
                status=record.status,
                idempotency_key=record.idempotency_key,
                schema_version=record.schema_version,
                access_scope=record.access_scope,
                source_version=record.source_version,
                cv_profile_version=record.cv_profile_version,
                position_profile_version=record.position_profile_version,
                taxonomy_version=record.taxonomy_version,
                graph_version=record.graph_version,
                algorithm_version=record.algorithm_version,
                matching_method=record.matching_method,
                degraded=record.degraded,
                overall_score=record.overall_score,
                error_code=record.error_code,
                error_message=record.error_message,
            )
            self._session.add(row)
        else:
            row.task_id = record.task_id
            row.evaluation_id = record.evaluation_id or row.evaluation_id
            row.user_id = record.user_id
            row.tenant_id = record.tenant_id
            row.resume_id = record.resume_id
            row.position_id = record.position_id
            row.provider = record.provider
            row.target_type = record.target_type
            row.status = record.status
            row.idempotency_key = record.idempotency_key
            row.schema_version = record.schema_version
            row.access_scope = record.access_scope or row.access_scope
            row.source_version = record.source_version
            row.cv_profile_version = record.cv_profile_version or row.cv_profile_version
            row.position_profile_version = (
                record.position_profile_version or row.position_profile_version
            )
            row.taxonomy_version = record.taxonomy_version
            row.graph_version = record.graph_version
            row.algorithm_version = record.algorithm_version
            row.matching_method = (
                record.matching_method or row.matching_method
            )
            row.degraded = (
                record.degraded if record.degraded is not None else row.degraded
            )
            row.overall_score = (
                record.overall_score
                if record.overall_score is not None
                else row.overall_score
            )
            row.error_code = record.error_code
            row.error_message = record.error_message
        self._session.flush()
        return self._service_reference(row)

    def add_learning_path(
        self, record: LearningPathRecordData
    ) -> LearningPathRecordData:
        row = LearningPathRecord(
            path_id=record.path_id,
            evaluation_id=record.evaluation_id,
            user_id=record.user_id,
            tenant_id=record.tenant_id,
            target_position_id=record.target_position_id,
            time_budget_hours=record.time_budget_hours,
            gap_analysis=dict(record.gap_analysis),
            status=record.status,
            provider=record.provider,
            algorithm_versions=dict(record.algorithm_versions),
            data_versions=dict(record.data_versions),
            versions=dict(record.versions),
            resume_id=record.resume_id,
            validated_cv_snapshot_id=record.validated_cv_snapshot_id,
            position_id=record.position_id,
        )
        self._session.add(row)
        self._session.flush()
        return self._learning_path(row)

    def get_learning_path(self, path_id: str) -> LearningPathRecordData | None:
        row = self._session.get(LearningPathRecord, path_id)
        return self._learning_path(row) if row is not None else None

    def list_learning_paths(self, user_id: str | None) -> list[LearningPathRecordData]:
        query = self._session.query(LearningPathRecord)
        if user_id is not None:
            query = query.filter(LearningPathRecord.user_id == user_id)
        return [
            self._learning_path(row)
            for row in query.order_by(LearningPathRecord.created_at.desc()).all()
        ]

    def get_service_reference(
        self, reference_id: str
    ) -> MatchingServiceReferenceRecord | None:
        row = (
            self._session.query(MatchingServiceReference)
            .filter(
                (MatchingServiceReference.task_id == reference_id)
                | (MatchingServiceReference.evaluation_id == reference_id)
            )
            .first()
        )
        return self._service_reference(row) if row is not None else None

    def list_service_references(
        self,
        user_id: str | None,
        *,
        position_id: str | None = None,
        target_type: str | None = None,
        include_orphan_intents: bool = False,
    ) -> list[MatchingServiceReferenceRecord]:
        query = self._session.query(MatchingServiceReference)
        if user_id is not None:
            query = query.filter(MatchingServiceReference.user_id == user_id)
        if position_id is not None:
            query = query.filter(MatchingServiceReference.position_id == position_id)
        if target_type is not None:
            query = query.filter(MatchingServiceReference.target_type == target_type)
        rows = query.order_by(MatchingServiceReference.created_at.desc()).all()
        intent_query = self._session.query(MatchingSubmissionIntent)
        if include_orphan_intents:
            if user_id is not None:
                intent_query = intent_query.filter(
                    MatchingSubmissionIntent.user_id == user_id
                )
            if position_id is not None:
                intent_query = intent_query.filter(
                    MatchingSubmissionIntent.position_id == position_id
                )
            if target_type is not None:
                intent_query = intent_query.filter(
                    MatchingSubmissionIntent.target_type == target_type
                )
        else:
            intent_query = intent_query.filter(
                MatchingSubmissionIntent.idempotency_key.in_(
                    [row.idempotency_key for row in rows]
                )
            )
        intents = intent_query.all()
        intent_by_key = {
            intent.idempotency_key: intent
            for intent in intents
        }
        records = [
            self._service_reference(
                row,
                error_code=(
                    intent_by_key[row.idempotency_key].last_error_code
                    if row.idempotency_key in intent_by_key
                    else None
                ),
            )
            for row in rows
        ]
        if not include_orphan_intents:
            return records
        reference_keys = {row.idempotency_key for row in rows}
        records.extend(
            self._intent_reference(intent)
            for intent in intents
            if intent.idempotency_key not in reference_keys
        )
        return records

    def save_intent(
        self,
        *,
        idempotency_key: str,
        user_id: str,
        tenant_id: str,
        resume_id: str,
        position_id: str,
        target_type: str,
        cv_profile_version: str,
        position_profile_version: str,
        status: str = "intended",
        access_scope: str = "",
        source_version: str = "legacy-unspecified",
        taxonomy_version: str = "legacy-unspecified",
        graph_version: str = "legacy-unspecified",
        algorithm_version: str = "legacy-unspecified",
    ) -> None:
        existing = (
            self._session.query(MatchingSubmissionIntent)
            .filter(MatchingSubmissionIntent.idempotency_key == idempotency_key)
            .first()
        )
        if existing is not None:
            return
        row = MatchingSubmissionIntent(
            idempotency_key=idempotency_key,
            user_id=user_id,
            tenant_id=tenant_id,
            resume_id=resume_id,
            position_id=position_id,
            target_type=target_type,
            cv_profile_version=cv_profile_version,
            position_profile_version=position_profile_version,
            status=status,
            access_scope=access_scope,
            source_version=source_version,
            taxonomy_version=taxonomy_version,
            graph_version=graph_version,
            algorithm_version=algorithm_version,
        )
        self._session.add(row)
        self._session.flush()

    def update_intent_status(
        self, idempotency_key: str, status: str, error_code: str = ""
    ) -> None:
        row = (
            self._session.query(MatchingSubmissionIntent)
            .filter(MatchingSubmissionIntent.idempotency_key == idempotency_key)
            .first()
        )
        if row is None:
            return
        row.status = status
        if error_code:
            row.last_error_code = error_code
        if status in ("remote_unknown", "reference_pending"):
            row.retry_count = (row.retry_count or 0) + 1
            row.next_retry_at = _datetime.now(_tz.utc) + _timedelta(
                seconds=min(60 * (2**row.retry_count), 3600)
            )
        self._session.flush()

    @staticmethod
    def _service_reference(
        row: MatchingServiceReference, *, error_code: str | None = None
    ) -> MatchingServiceReferenceRecord:
        return MatchingServiceReferenceRecord(
            task_id=row.task_id,
            evaluation_id=row.evaluation_id,
            user_id=row.user_id,
            tenant_id=row.tenant_id,
            resume_id=row.resume_id,
            position_id=row.position_id,
            provider=row.provider,
            target_type=getattr(row, "target_type", "standard_position"),
            status=row.status,
            idempotency_key=row.idempotency_key,
            created_at=row.created_at,
            updated_at=row.updated_at,
            schema_version=row.schema_version,
            access_scope=row.access_scope,
            source_version=row.source_version,
            cv_profile_version=row.cv_profile_version,
            position_profile_version=row.position_profile_version,
            taxonomy_version=row.taxonomy_version,
            graph_version=row.graph_version,
            algorithm_version=row.algorithm_version,
            matching_method=row.matching_method,
            degraded=row.degraded,
            overall_score=row.overall_score,
            error_code=getattr(row, "error_code", None) or error_code,
            error_message=getattr(row, "error_message", None),
        )

    @staticmethod
    def _learning_path(row: LearningPathRecord) -> LearningPathRecordData:
        created_at = row.created_at
        updated_at = row.updated_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=_tz.utc)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=_tz.utc)
        return LearningPathRecordData(
            path_id=row.path_id,
            evaluation_id=row.evaluation_id,
            user_id=row.user_id,
            tenant_id=row.tenant_id,
            target_position_id=row.target_position_id,
            time_budget_hours=row.time_budget_hours,
            gap_analysis=dict(row.gap_analysis),
            status=row.status,
            provider=row.provider,
            algorithm_versions=dict(row.algorithm_versions or {}),
            data_versions=dict(row.data_versions or {}),
            versions=dict(row.versions or {}),
            resume_id=row.resume_id,
            validated_cv_snapshot_id=row.validated_cv_snapshot_id,
            position_id=row.position_id,
            created_at=created_at,
            updated_at=updated_at,
        )

    @staticmethod
    def _intent_reference(
        intent: MatchingSubmissionIntent,
    ) -> MatchingServiceReferenceRecord:
        statuses = {
            "intended": "pending",
            "rejected": "failed",
            "remote_unknown": "running",
            "reference_pending": "running",
            "reference_saved": "failed",
            "abandoned": "failed",
        }
        return MatchingServiceReferenceRecord(
            task_id="",
            evaluation_id=None,
            user_id=intent.user_id,
            tenant_id=intent.tenant_id,
            resume_id=intent.resume_id,
            position_id=intent.position_id,
            provider="matching-submission-intent",
            target_type=intent.target_type,
            status=statuses.get(intent.status, "failed"),
            idempotency_key=intent.idempotency_key,
            created_at=intent.created_at,
            updated_at=intent.updated_at,
            schema_version=intent.schema_version,
            access_scope=intent.access_scope,
            source_version=intent.source_version,
            cv_profile_version=intent.cv_profile_version,
            position_profile_version=intent.position_profile_version,
            taxonomy_version=intent.taxonomy_version,
            graph_version=intent.graph_version,
            algorithm_version=intent.algorithm_version,
            matching_method=None,
            degraded=None,
            overall_score=None,
            error_code=intent.last_error_code,
        )


class SqlAlchemyMatchingUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyMatchingUnitOfWork":
        self._session = self._session_factory()
        self.matching = SqlAlchemyMatchingRepository(self._session)
        self._tasks = SqlAlchemyTaskRepository(self._session)
        return self

    def add_task(self, record: TaskRecord) -> None:
        self._tasks.add(record)

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
