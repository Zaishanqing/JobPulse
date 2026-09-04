from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker
from uuid import uuid4

from app.models.trend_source import TrendSource
from app.models.predicted_position import PredictedPosition
from app.models.predicted_position_workflow import (
    PredictedPositionDefinitionVersion,
    PredictedPositionMatch,
    PredictedPositionRelationVersion,
)
from app.models.emerging_position import EmergingPosition
from app.models.review_task import ReviewTask
from app.models.review_task_event import ReviewTaskEvent
from app.models.skill import Skill
from app.models.skill_alias import SkillAlias
from app.models.skill_normalization_candidate import SkillNormalizationCandidate
from app.models.standard_position import StandardPosition
from app.models.task_record import TaskRecord as TaskRow
from app.infrastructure.tasks import SqlAlchemyTaskRepository
from app.contexts.tasks import TaskRecord
from app.contexts.market_intelligence import PredictedPositionRecord, TrendSourceDraft, TrendSourceRecord
from app.contexts.market_intelligence.ports import (
    PositionComparisonProfile,
    PredictionDefinitionRecord,
    PredictionMatchRecord,
    PredictionRelationRecord,
)
from app.domain.json_types import freeze_json_object


class SqlAlchemyTrendSourceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, draft: TrendSourceDraft) -> TrendSourceRecord:
        row = TrendSource(
            source_type=draft.source_type, title=draft.title,
            source_name=draft.source_name, url=draft.url, raw_text=draft.raw_text,
            publish_date=draft.publish_date, credibility_score=draft.credibility_score,
            parsed_keywords=list(draft.parsed_keywords),
        )
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    def list(self) -> list[TrendSourceRecord]:
        rows = self._session.query(TrendSource).order_by(TrendSource.created_at.desc()).all()
        return [self._record(row) for row in rows]

    def get(self, source_id: str) -> TrendSourceRecord | None:
        row = self._session.get(TrendSource, source_id)
        return self._record(row) if row is not None else None

    def update(self, source_id: str, changes: dict[str, object]) -> TrendSourceRecord:
        row = self._required(source_id)
        for key, value in changes.items():
            setattr(row, key, value)
        self._session.flush()
        return self._record(row)

    def delete(self, source_id: str) -> None:
        self._session.delete(self._required(source_id))

    def get_by_provider_snapshot(self, provider_run_id: str, snapshot_reference: str) -> TrendSourceRecord | None:
        row = self._session.query(TrendSource).filter(TrendSource.provider_run_id == provider_run_id, TrendSource.snapshot_reference == snapshot_reference).one_or_none()
        return self._record(row) if row else None

    def add_projection(self, values: dict[str, object]) -> TrendSourceRecord:
        existing = self.get_by_provider_snapshot(str(values["provider_run_id"]), str(values["snapshot_reference"]))
        if existing:
            return existing
        row = TrendSource(**values)
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    def _required(self, source_id: str) -> TrendSource:
        row = self._session.get(TrendSource, source_id)
        if row is None:
            raise LookupError(source_id)
        return row

    @staticmethod
    def _record(row: TrendSource) -> TrendSourceRecord:
        return TrendSourceRecord(
            row.id, row.source_type, row.title, row.source_name, row.url,
            row.raw_text, row.publish_date, row.credibility_score,
            tuple(row.parsed_keywords or ()), row.created_at, row.updated_at,
            row.provider_run_id, row.external_source_id, row.source_version,
            row.captured_at, row.snapshot_reference,
            row.extraction_version, dict(row.source_metadata or {}),
        )


class SqlAlchemyPredictedPositionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
    def add(self, values: dict[str, object]) -> PredictedPositionRecord:
        row = PredictedPosition(**values)
        self._session.add(row)
        self._session.flush()
        return self._record(row)
    def get(self, predicted_id: str) -> PredictedPositionRecord | None:
        row = self._session.get(PredictedPosition, predicted_id)
        return self._record(row) if row is not None else None
    def list(self) -> list[PredictedPositionRecord]:
        return [self._record(row) for row in self._session.query(PredictedPosition).order_by(PredictedPosition.created_at.desc()).all()]
    def update(self, predicted_id: str, changes: dict[str, object]) -> PredictedPositionRecord:
        row = self._session.get(PredictedPosition, predicted_id)
        if row is None:
            raise LookupError(predicted_id)
        for key, value in changes.items():
            setattr(row, key, value)
        self._session.flush()
        return self._record(row)
    def get_by_provider_candidate(self, provider_run_id: str, candidate_key: str) -> PredictedPositionRecord | None:
        row = self._session.query(PredictedPosition).filter(PredictedPosition.provider_run_id == provider_run_id, PredictedPosition.candidate_key == candidate_key).one_or_none()
        return self._record(row) if row else None

    @staticmethod
    def _skill_values(values) -> tuple[tuple[str, ...], tuple[str, ...]]:
        ids: list[str] = []
        names: list[str] = []
        for value in values or ():
            if isinstance(value, dict):
                skill_id = value.get("skill_id") or value.get("normalized_skill_id")
                name = value.get("skill_name") or value.get("name") or value.get("raw_skill")
                if skill_id:
                    ids.append(str(skill_id))
                if name:
                    names.append(str(name))
            elif value:
                names.append(str(value))
        return tuple(ids), tuple(names)

    def comparison_profiles(self, predicted_id: str):
        current = self._session.get(PredictedPosition, predicted_id)
        if current is None:
            raise LookupError(predicted_id)
        current_ids, current_names = self._skill_values(current.potential_skills)
        source = PositionComparisonProfile(
            "predicted_position", current.id, current.position_name,
            current_ids, current_names,
            tuple(current.potential_responsibilities or ()),
            tuple(current.industry_scenarios or ()),
            tuple(current.evidence_references or ()),
        )
        targets: list[PositionComparisonProfile] = []
        for row in self._session.query(StandardPosition).all():
            required_ids, required_names = self._skill_values(row.required_skills)
            bonus_ids, bonus_names = self._skill_values(row.bonus_skills)
            targets.append(PositionComparisonProfile(
                "standard_position", row.id, row.position_name,
                (*required_ids, *bonus_ids), (*required_names, *bonus_names),
                tuple(row.core_responsibilities or ()),
                tuple(row.industry_scenarios or ()), (),
            ))
        for row in self._session.query(EmergingPosition).filter(EmergingPosition.status == "published").all():
            required_ids, required_names = self._skill_values(row.required_skills)
            bonus_ids, bonus_names = self._skill_values(row.bonus_skills)
            targets.append(PositionComparisonProfile(
                "emerging_position", row.id, row.position_name,
                (*required_ids, *bonus_ids), (*required_names, *bonus_names),
                tuple(row.core_responsibilities or ()),
                tuple(row.industry_scenarios or ()),
                tuple(str(value) for value in row.evidence_jd_ids or ()),
            ))
        for row in self._session.query(PredictedPosition).filter(PredictedPosition.id != predicted_id).all():
            ids, names = self._skill_values(row.potential_skills)
            targets.append(PositionComparisonProfile(
                "predicted_position", row.id, row.position_name, ids, names,
                tuple(row.potential_responsibilities or ()),
                tuple(row.industry_scenarios or ()),
                tuple(row.evidence_references or ()),
            ))
        return source, tuple(targets)

    def skill_catalog_version(self) -> str:
        skill_count, skill_updated = self._session.query(
            func.count(Skill.id),
            func.max(Skill.updated_at),
        ).one()
        alias_count, alias_updated = self._session.query(
            func.count(SkillAlias.id),
            func.max(SkillAlias.updated_at),
        ).one()
        return (
            f"skills:{skill_count}:{skill_updated.isoformat() if skill_updated else ''}:"
            f"aliases:{alias_count}:{alias_updated.isoformat() if alias_updated else ''}"
        )

    def normalize_skills(self, names: tuple[str, ...], *, context: str):
        skills = {row.skill_name.casefold(): row for row in self._session.query(Skill).all()}
        aliases = {
            row.alias.casefold(): self._session.get(Skill, row.skill_id)
            for row in self._session.query(SkillAlias).all()
        }
        result = []
        for name in dict.fromkeys(item.strip() for item in names if item.strip()):
            target = skills.get(name.casefold()) or aliases.get(name.casefold())
            if target is None:
                candidate = (
                    self._session.query(SkillNormalizationCandidate)
                    .filter(
                        func.lower(SkillNormalizationCandidate.raw_skill) == name.casefold(),
                        SkillNormalizationCandidate.context == context,
                        SkillNormalizationCandidate.status == "pending",
                    )
                    .one_or_none()
                )
                if candidate is None:
                    candidate = SkillNormalizationCandidate(
                        raw_skill=name, candidate_skill_id=None, confidence=0.0,
                        context=context, status="pending",
                    )
                    self._session.add(candidate)
                    self._session.flush()
                result.append(freeze_json_object({
                    "skill_id": None, "skill_name": name,
                    "resolution_status": "unresolved",
                    "normalization_candidate_id": candidate.id,
                }))
            else:
                result.append(freeze_json_object({
                    "skill_id": target.id, "skill_name": target.skill_name,
                    "resolution_status": "resolved",
                    "normalization_candidate_id": None,
                }))
        return tuple(result)

    def save_matches(self, predicted_id, values, actor_id, *, cache_key="legacy"):
        version = (self._session.query(func.max(PredictedPositionMatch.version)).filter(
            PredictedPositionMatch.predicted_position_id == predicted_id).scalar() or 0) + 1
        rows = []
        for value in values:
            row = PredictedPositionMatch(
                predicted_position_id=predicted_id, version=version,
                created_by=actor_id, cache_key=cache_key, **dict(value)
            )
            self._session.add(row)
            rows.append(row)
        self._session.flush()
        return tuple(self._match_record(row) for row in rows)

    def list_matches(self, predicted_id):
        rows = self._session.query(PredictedPositionMatch).filter(
            PredictedPositionMatch.predicted_position_id == predicted_id
        ).order_by(PredictedPositionMatch.version.desc(), PredictedPositionMatch.similarity_score.desc()).all()
        return tuple(self._match_record(row) for row in rows)

    def save_definition(self, predicted_id, payload, actor_id, *, cache_key="legacy"):
        version = (self._session.query(func.max(PredictedPositionDefinitionVersion.version)).filter(
            PredictedPositionDefinitionVersion.predicted_position_id == predicted_id).scalar() or 0) + 1
        row = PredictedPositionDefinitionVersion(
            predicted_position_id=predicted_id, version=version,
            definition_payload=dict(payload),
            status="draft", created_by=actor_id, cache_key=cache_key,
        )
        self._session.add(row)
        self._session.flush()
        return self._definition_record(row)

    def list_definitions(self, predicted_id):
        rows = self._session.query(PredictedPositionDefinitionVersion).filter(
            PredictedPositionDefinitionVersion.predicted_position_id == predicted_id
        ).order_by(PredictedPositionDefinitionVersion.version.desc()).all()
        return tuple(self._definition_record(row) for row in rows)

    def get_definition(self, definition_id):
        row = self._session.get(PredictedPositionDefinitionVersion, definition_id)
        return self._definition_record(row) if row else None

    def attach_review(self, definition_id, review_task_id):
        row = self._session.get(PredictedPositionDefinitionVersion, definition_id)
        if row is None:
            raise LookupError(definition_id)
        row.review_task_id = review_task_id
        row.status = "in_review"
        self._session.flush()
        return self._definition_record(row)

    def create_definition_review(self, definition_id, actor_id, reason):
        definition = self._session.get(PredictedPositionDefinitionVersion, definition_id)
        if definition is None:
            raise LookupError(definition_id)
        if definition.status == "published":
            raise RuntimeError("Published definition versions are immutable")
        if definition.review_task_id:
            return self._definition_record(definition)
        review = ReviewTask(
            object_type="predicted_position_definition",
            object_id=definition.id,
            priority="normal",
            reason=reason,
            status="pending",
        )
        self._session.add(review)
        self._session.flush()
        self._session.add(ReviewTaskEvent(
            task_id=review.id, actor_user_id=actor_id, action="create",
            before_status=None, after_status="pending", comment=reason,
            payload_snapshot={"definition_id": definition.id},
        ))
        definition.review_task_id = review.id
        definition.status = "in_review"
        self._session.flush()
        return self._definition_record(definition)

    def review_status(self, review_task_id):
        row = self._session.get(ReviewTask, review_task_id)
        if row is None:
            return None
        return freeze_json_object({
            "review_task_id": row.id, "status": row.status,
            "reviewer_id": row.reviewer_id, "review_comment": row.review_comment,
        })

    def publication_facts(self, predicted_id, definition_id):
        prediction = self._session.get(PredictedPosition, predicted_id)
        definition = self._session.get(PredictedPositionDefinitionVersion, definition_id)
        if prediction is None or definition is None or definition.predicted_position_id != predicted_id:
            raise LookupError(predicted_id)
        review = self._session.get(ReviewTask, definition.review_task_id) if definition.review_task_id else None
        task = None
        for row in self._session.query(TaskRow).filter(TaskRow.task_type == "predicted_position_analysis").all():
            if (row.result_payload or {}).get("provider_run_id") == prediction.provider_run_id:
                task = row
                break
        return freeze_json_object({
            "prediction_status": prediction.status,
            "provider_run_id": prediction.provider_run_id,
            "task_status": task.status if task else None,
            "task_result": (task.result_payload or {}) if task else {},
            "definition": definition.definition_payload,
            "definition_status": definition.status,
            "review_status": review.status if review else None,
            "review_task_id": review.id if review else None,
        })

    def publish_definition(self, predicted_id, definition_id, published_at):
        prediction = self._session.get(PredictedPosition, predicted_id)
        definition = self._session.get(PredictedPositionDefinitionVersion, definition_id)
        if prediction is None or definition is None:
            raise LookupError(predicted_id)
        definition.status = "published"
        prediction.status = "published"
        prediction.published_definition_version_id = definition.id
        prediction.published_at = published_at
        self._session.flush()
        return self._record(prediction)

    def reject_definition(self, definition_id):
        row = self._session.get(PredictedPositionDefinitionVersion, definition_id)
        if row is None:
            raise LookupError(definition_id)
        if row.status == "published":
            raise RuntimeError("Published definition versions are immutable")
        row.status = "rejected"
        prediction = self._session.get(PredictedPosition, row.predicted_position_id)
        if prediction is not None:
            prediction.status = "rejected"
        self._session.flush()
        return self._definition_record(row)

    def save_relation(self, predicted_id, relation_type, target_id, reason, actor_id, *, deleted=False, relation_identity_id=None, supersedes_relation_id=None):
        if self._session.get(PredictedPosition, predicted_id) is None:
            raise LookupError(predicted_id)
        if relation_type == "standard_position" and self._session.get(StandardPosition, target_id) is None:
            raise LookupError(target_id)
        if relation_type == "emerging_position" and self._session.get(EmergingPosition, target_id) is None:
            raise LookupError(target_id)
        if relation_type == "independent":
            target_id = None
        version = (self._session.query(func.max(PredictedPositionRelationVersion.version)).filter(
            PredictedPositionRelationVersion.predicted_position_id == predicted_id).scalar() or 0) + 1
        row = PredictedPositionRelationVersion(
            predicted_position_id=predicted_id, version=version,
            relation_type=relation_type, target_id=target_id,
            status="deleted" if deleted else "active", reason=reason,
            created_by=actor_id,
            relation_identity_id=relation_identity_id or str(uuid4()),
            supersedes_relation_id=supersedes_relation_id,
        )
        self._session.add(row)
        self._session.flush()
        return self._relation_record(row)

    def list_relations(self, predicted_id):
        rows = self._session.query(PredictedPositionRelationVersion).filter(
            PredictedPositionRelationVersion.predicted_position_id == predicted_id
        ).order_by(PredictedPositionRelationVersion.version.desc()).all()
        latest_by_identity: dict[str, object] = {}
        for row in rows:
            identity = row.relation_identity_id or row.id
            latest_by_identity.setdefault(identity, row)
        return tuple(
            self._relation_record(row)
            for row in latest_by_identity.values()
            if row.status == "active"
        )

    def list_relation_history(self, predicted_id):
        rows = self._session.query(PredictedPositionRelationVersion).filter(
            PredictedPositionRelationVersion.predicted_position_id == predicted_id
        ).order_by(PredictedPositionRelationVersion.version.desc()).all()
        return tuple(self._relation_record(row) for row in rows)

    def get_relation(self, relation_id):
        row = self._session.get(PredictedPositionRelationVersion, relation_id)
        return self._relation_record(row) if row else None

    @staticmethod
    def _match_record(row):
        return PredictionMatchRecord(
            row.id, row.predicted_position_id, row.version, row.target_type,
            row.target_id, row.similarity_score, tuple(row.matched_skills or ()),
            tuple(row.missing_skills or ()), freeze_json_object(row.overlap_evidence or {}),
            row.recommendation, row.created_at, row.cache_key,
        )

    @staticmethod
    def _definition_record(row):
        return PredictionDefinitionRecord(
            row.id, row.predicted_position_id, row.version, row.status,
            freeze_json_object(row.definition_payload), row.review_task_id, row.created_at,
            row.cache_key,
        )

    @staticmethod
    def _relation_record(row):
        return PredictionRelationRecord(
            row.id, row.predicted_position_id, row.version, row.relation_type,
            row.target_id, row.status, row.reason, row.created_at,
            row.relation_identity_id, row.supersedes_relation_id,
        )
    @staticmethod
    def _record(row: PredictedPosition) -> PredictedPositionRecord:
        return PredictedPositionRecord(
            row.id, row.position_name, tuple(row.prediction_basis or ()),
            tuple(row.related_source_ids or ()), tuple(row.potential_responsibilities or ()),
            tuple(row.potential_skills or ()), tuple(row.industry_scenarios or ()),
            row.confidence_score, row.status, row.created_at, row.updated_at,
            row.provider_run_id, row.candidate_key, row.industry_domain,
            row.emergence_score, dict(row.score_components or {}),
            row.algorithm_version, row.formula_version, row.window_start,
            row.window_end, row.source_coverage, tuple(row.missing_sources or ()),
            tuple(row.quality_flags or ()), tuple(row.evidence_references or ()),
            row.published_definition_version_id, row.published_at,
        )


class SqlAlchemyTrendUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyTrendUnitOfWork":
        self._session = self._session_factory()
        self.sources = SqlAlchemyTrendSourceRepository(self._session)
        self.predictions = SqlAlchemyPredictedPositionRepository(self._session)
        self._tasks = SqlAlchemyTaskRepository(self._session)
        return self

    def add_task(self, record: TaskRecord) -> None:
        self._tasks.add(record)

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def save_task(self, record: TaskRecord) -> None:
        self._tasks.save(record)

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
