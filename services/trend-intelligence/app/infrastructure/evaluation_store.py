from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.models import (
    EvaluationDatasetEventModel,
    EvaluationDatasetModel,
    EvaluationLabelModel,
    EvaluationSampleModel,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DatasetRuleViolation(ValueError):
    pass


class SqlAlchemyEvaluationDatasetStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    @staticmethod
    def _dataset(row: EvaluationDatasetModel) -> dict[str, object]:
        return {"id": row.id, "name": row.name, "version": row.version, "status": row.status,
                "parent_dataset_id": row.parent_dataset_id, "description": row.description,
                "created_by": row.created_by, "published_by": row.published_by,
                "created_at": row.created_at, "published_at": row.published_at}

    @staticmethod
    def _sample(row: EvaluationSampleModel) -> dict[str, object]:
        return {"id": row.id, "dataset_id": row.dataset_id, "sample_key": row.sample_key,
                "entity_type": row.entity_type, "entity_id": row.entity_id,
                "entity_name": row.entity_name, "prediction_cutoff": row.prediction_cutoff,
                "label_window_start": row.label_window_start, "label_window_end": row.label_window_end,
                "source_type": row.source_type, "source_reference": row.source_reference,
                "source_dedup_key": row.source_dedup_key, "evidence": row.evidence,
                "quality_flags": row.quality_flags, "status": row.status,
                "created_at": row.created_at}

    @staticmethod
    def _label(row: EvaluationLabelModel) -> dict[str, object]:
        return {"id": row.id, "sample_id": row.sample_id, "version": row.version,
                "label_type": row.label_type, "direction": row.direction,
                "observed_value": row.observed_value, "evidence": row.evidence,
                "confidence_level": row.confidence_level, "annotator_id": row.annotator_id,
                "reviewer_id": row.reviewer_id, "review_note": row.review_note,
                "status": row.status, "created_at": row.created_at, "reviewed_at": row.reviewed_at}

    def _event(self, session: Session, dataset_id: str, action: str, actor: str, *, sample_id=None, label_id=None, details=None) -> None:
        session.add(EvaluationDatasetEventModel(
            dataset_id=dataset_id, sample_id=sample_id, label_id=label_id,
            action=action, actor=actor, details=details or {},
        ))

    def create_dataset(self, value: dict[str, object]) -> dict[str, object]:
        with self.sessions.begin() as session:
            exists = session.scalar(select(EvaluationDatasetModel.id).where(
                EvaluationDatasetModel.name == value["name"],
                EvaluationDatasetModel.version == value["version"],
            ))
            if exists:
                raise DatasetRuleViolation("dataset version already exists")
            row = EvaluationDatasetModel(
                name=str(value["name"]), version=str(value["version"]),
                description=value.get("description"), created_by=str(value["created_by"]),
            )
            session.add(row)
            session.flush()
            self._event(session, row.id, "dataset_created", row.created_by,
                        details={"version": row.version})
            return self._dataset(row)

    def revise_dataset(self, dataset_id: str, version: str, actor: str) -> dict[str, object] | None:
        with self.sessions.begin() as session:
            parent = session.get(EvaluationDatasetModel, dataset_id)
            if parent is None:
                return None
            if parent.status != "published":
                raise DatasetRuleViolation("only a published dataset can be revised")
            exists = session.scalar(select(EvaluationDatasetModel.id).where(
                EvaluationDatasetModel.name == parent.name,
                EvaluationDatasetModel.version == version,
            ))
            if exists:
                raise DatasetRuleViolation("dataset version already exists")
            row = EvaluationDatasetModel(
                name=parent.name, version=version, status="draft",
                parent_dataset_id=parent.id, description=parent.description, created_by=actor,
            )
            session.add(row)
            session.flush()
            samples = session.scalars(select(EvaluationSampleModel).where(
                EvaluationSampleModel.dataset_id == parent.id
            ))
            for item in samples:
                session.add(EvaluationSampleModel(
                    dataset_id=row.id, sample_key=item.sample_key, entity_type=item.entity_type,
                    entity_id=item.entity_id, entity_name=item.entity_name,
                    prediction_cutoff=item.prediction_cutoff,
                    label_window_start=item.label_window_start, label_window_end=item.label_window_end,
                    source_type=item.source_type, source_reference=item.source_reference,
                    source_dedup_key=item.source_dedup_key, evidence=item.evidence,
                    quality_flags=item.quality_flags, status="pending",
                ))
            self._event(session, row.id, "dataset_revision_created", actor,
                        details={"parent_dataset_id": parent.id})
            return self._dataset(row)

    def add_samples(self, dataset_id: str, source_type: str, records: list[dict[str, object]], actor: str) -> list[dict[str, object]]:
        created: list[dict[str, object]] = []
        with self.sessions.begin() as session:
            dataset = session.get(EvaluationDatasetModel, dataset_id)
            if dataset is None:
                raise LookupError("dataset not found")
            if dataset.status == "published":
                raise DatasetRuleViolation("published datasets are immutable")
            for value in records:
                evidence = list(value.get("evidence") or [])
                flags = []
                if any(not item.get("observed_at") for item in evidence):
                    flags.append("missing_evidence_timestamp")
                dedup_key = str(value.get("source_dedup_key") or f"{source_type}:{value['source_reference']}:{value['entity_id']}")
                sample_key = str(value.get("sample_key") or f"{value['entity_type']}:{value['entity_id']}:{value['label_window_end']}")
                exists = session.scalar(select(EvaluationSampleModel.id).where(
                    EvaluationSampleModel.dataset_id == dataset_id,
                    EvaluationSampleModel.sample_key == sample_key,
                ))
                if exists:
                    continue
                row = EvaluationSampleModel(
                    dataset_id=dataset_id, sample_key=sample_key,
                    entity_type=str(value["entity_type"]), entity_id=str(value["entity_id"]),
                    entity_name=str(value["entity_name"]), prediction_cutoff=value["prediction_cutoff"],
                    label_window_start=value["label_window_start"],
                    label_window_end=value["label_window_end"], source_type=source_type,
                    source_reference=str(value["source_reference"]), source_dedup_key=dedup_key,
                    evidence=evidence, quality_flags=flags,
                )
                session.add(row)
                session.flush()
                self._event(session, dataset_id, "sample_generated", actor, sample_id=row.id,
                            details={"source_type": source_type})
                created.append(self._sample(row))
            dataset.status = "labeling"
        return created

    def list_samples(self, dataset_id: str, status: str | None = None) -> list[dict[str, object]] | None:
        with self.sessions() as session:
            if session.get(EvaluationDatasetModel, dataset_id) is None:
                return None
            query = select(EvaluationSampleModel).where(EvaluationSampleModel.dataset_id == dataset_id)
            if status:
                query = query.where(EvaluationSampleModel.status == status)
            return [self._sample(row) for row in session.scalars(query.order_by(EvaluationSampleModel.sample_key))]

    def submit_label(self, sample_id: str, value: dict[str, object]) -> dict[str, object] | None:
        with self.sessions.begin() as session:
            sample = session.get(EvaluationSampleModel, sample_id)
            if sample is None:
                return None
            dataset = session.get(EvaluationDatasetModel, sample.dataset_id)
            if dataset.status == "published":
                raise DatasetRuleViolation("published datasets are immutable")
            version = int(session.scalar(select(func.max(EvaluationLabelModel.version)).where(
                EvaluationLabelModel.sample_id == sample_id
            )) or 0) + 1
            existing = list(session.scalars(select(EvaluationLabelModel).where(
                EvaluationLabelModel.sample_id == sample_id,
                EvaluationLabelModel.status.in_(("submitted", "conflict")),
            )))
            conflict = any(
                item.direction != value["direction"] or item.observed_value != value.get("observed_value")
                for item in existing
            )
            row = EvaluationLabelModel(
                sample_id=sample_id, version=version, label_type=str(value["label_type"]),
                direction=str(value["direction"]), observed_value=value.get("observed_value"),
                evidence=list(value["evidence"]), confidence_level=str(value["confidence_level"]),
                annotator_id=str(value["annotator_id"]), status="conflict" if conflict else "submitted",
            )
            session.add(row)
            session.flush()
            if conflict:
                for item in existing:
                    item.status = "conflict"
                sample.status = "conflict"
                action = "label_conflict_detected"
            else:
                sample.status = "annotated"
                action = "label_submitted"
            self._event(session, sample.dataset_id, action, row.annotator_id,
                        sample_id=sample.id, label_id=row.id)
            return self._label(row)

    def review_label(self, label_id: str, decision: str, reviewer: str, note: str | None) -> dict[str, object] | None:
        with self.sessions.begin() as session:
            row = session.get(EvaluationLabelModel, label_id)
            if row is None:
                return None
            sample = session.get(EvaluationSampleModel, row.sample_id)
            dataset = session.get(EvaluationDatasetModel, sample.dataset_id)
            if dataset.status == "published":
                raise DatasetRuleViolation("published datasets are immutable")
            if reviewer == row.annotator_id:
                raise DatasetRuleViolation("annotation creator cannot review their own label")
            if decision not in {"approve", "reject"}:
                raise DatasetRuleViolation("unsupported review decision")
            row.status = "approved" if decision == "approve" else "rejected"
            row.reviewer_id = reviewer
            row.review_note = note
            row.reviewed_at = utc_now()
            if decision == "approve":
                was_conflict = sample.status == "conflict"
                others = session.scalars(select(EvaluationLabelModel).where(
                    EvaluationLabelModel.sample_id == sample.id,
                    EvaluationLabelModel.id != row.id,
                    EvaluationLabelModel.status.in_(("submitted", "conflict")),
                ))
                for other in others:
                    other.status = "rejected"
                    other.reviewer_id = reviewer
                    other.review_note = "resolved by approval of another label"
                    other.reviewed_at = utc_now()
                sample.status = "approved"
                action = "label_conflict_resolved" if was_conflict else "label_approved"
            else:
                sample.status = "pending"
                action = "label_rejected"
            self._event(session, sample.dataset_id, action, reviewer,
                        sample_id=sample.id, label_id=row.id, details={"note": note})
            return self._label(row)

    def publish_dataset(self, dataset_id: str, actor: str) -> dict[str, object] | None:
        with self.sessions.begin() as session:
            row = session.get(EvaluationDatasetModel, dataset_id)
            if row is None:
                return None
            if row.status == "published":
                return self._dataset(row)
            statuses = list(session.scalars(select(EvaluationSampleModel.status).where(
                EvaluationSampleModel.dataset_id == dataset_id
            )))
            if not statuses or any(status != "approved" for status in statuses):
                raise DatasetRuleViolation("all samples require an approved label before publication")
            row.status = "published"
            row.published_by = actor
            row.published_at = utc_now()
            self._event(session, row.id, "dataset_published", actor,
                        details={"version": row.version, "sample_count": len(statuses)})
            return self._dataset(row)

    def get_dataset(self, dataset_id: str) -> dict[str, object] | None:
        with self.sessions() as session:
            row = session.get(EvaluationDatasetModel, dataset_id)
            return self._dataset(row) if row else None

    def history(self, dataset_id: str) -> list[dict[str, object]] | None:
        with self.sessions() as session:
            if session.get(EvaluationDatasetModel, dataset_id) is None:
                return None
            rows = session.scalars(select(EvaluationDatasetEventModel).where(
                EvaluationDatasetEventModel.dataset_id == dataset_id
            ).order_by(EvaluationDatasetEventModel.created_at, EvaluationDatasetEventModel.id))
            return [{
                "id": row.id, "dataset_id": row.dataset_id, "sample_id": row.sample_id,
                "label_id": row.label_id, "action": row.action, "actor": row.actor,
                "details": row.details, "created_at": row.created_at,
            } for row in rows]

    def published_ground_truth(self, dataset_id: str) -> tuple[dict[str, object], list[dict[str, object]]] | None:
        with self.sessions() as session:
            dataset = session.get(EvaluationDatasetModel, dataset_id)
            if dataset is None or dataset.status != "published":
                return None
            rows = session.execute(
                select(EvaluationSampleModel, EvaluationLabelModel)
                .join(EvaluationLabelModel, EvaluationLabelModel.sample_id == EvaluationSampleModel.id)
                .where(EvaluationSampleModel.dataset_id == dataset_id,
                       EvaluationLabelModel.status == "approved")
            )
            labels = [{
                "candidate_key": sample.entity_name, "entity_id": sample.entity_id,
                "entity_type": sample.entity_type, "direction": label.direction,
                "observed_value": label.observed_value, "prediction_cutoff": sample.prediction_cutoff,
                "label_window_start": sample.label_window_start,
                "label_window_end": sample.label_window_end,
                "source_dedup_key": sample.source_dedup_key,
                "sample_evidence": sample.evidence, "label_evidence": label.evidence,
                "quality_flags": sample.quality_flags, "confidence_level": label.confidence_level,
            } for sample, label in rows]
            return self._dataset(dataset), labels
