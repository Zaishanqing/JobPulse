from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.credibility import CONFIG_TYPES
from app.infrastructure.models import (
    AlgorithmConfigurationEventModel,
    AlgorithmConfigurationModel,
    BacktestRunModel,
    BacktestSliceResultModel,
    ExtractedTermModel,
    SourceSnapshotModel,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConfigurationConflict(ValueError):
    pass


class SqlAlchemyCredibilityStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def ensure_seeded(self) -> None:
        seed_path = Path(__file__).with_name("config_seed.json")
        configurations = json.loads(seed_path.read_text(encoding="utf-8"))["configurations"]
        with self.sessions.begin() as session:
            if session.scalar(select(AlgorithmConfigurationModel.id).limit(1)) is not None:
                return
            for value in configurations:
                row = AlgorithmConfigurationModel(
                    config_type=value["config_type"], version=value["version"],
                    payload=value["payload"], status="active", created_by="system-seed",
                    reviewed_by="system-migration", review_note="Initial migrated configuration",
                    activated_at=utc_now(),
                )
                session.add(row)
                session.flush()
                session.add(AlgorithmConfigurationEventModel(
                    configuration_id=row.id, action="seed_and_enable", actor="system-migration",
                    details={"version": row.version},
                ))

    @staticmethod
    def _data(row: AlgorithmConfigurationModel) -> dict[str, object]:
        return {
            "id": row.id, "config_type": row.config_type, "version": row.version,
            "payload": row.payload, "status": row.status, "created_by": row.created_by,
            "reviewed_by": row.reviewed_by, "review_note": row.review_note,
            "created_at": row.created_at, "activated_at": row.activated_at,
            "deactivated_at": row.deactivated_at,
        }

    def create_configuration(self, value: dict[str, object]) -> dict[str, object]:
        if value["config_type"] not in CONFIG_TYPES:
            raise ValueError("unsupported configuration type")
        with self.sessions.begin() as session:
            exists = session.scalar(select(AlgorithmConfigurationModel.id).where(
                AlgorithmConfigurationModel.config_type == value["config_type"],
                AlgorithmConfigurationModel.version == value["version"],
            ))
            if exists:
                raise ConfigurationConflict("configuration version already exists")
            row = AlgorithmConfigurationModel(
                config_type=str(value["config_type"]), version=str(value["version"]),
                payload=dict(value["payload"]), status="draft", created_by=str(value["created_by"]),
            )
            session.add(row)
            session.flush()
            session.add(AlgorithmConfigurationEventModel(
                configuration_id=row.id, action="created", actor=row.created_by,
                details={"config_version": row.version},
            ))
            return self._data(row)

    def list_configurations(self, config_type: str | None = None) -> list[dict[str, object]]:
        with self.sessions() as session:
            query = select(AlgorithmConfigurationModel)
            if config_type:
                query = query.where(AlgorithmConfigurationModel.config_type == config_type)
            rows = session.scalars(query.order_by(
                AlgorithmConfigurationModel.config_type,
                AlgorithmConfigurationModel.created_at.desc(),
            ))
            return [self._data(row) for row in rows]

    def get_configuration(self, config_id: str) -> dict[str, object] | None:
        with self.sessions() as session:
            row = session.get(AlgorithmConfigurationModel, config_id)
            return self._data(row) if row else None

    def active_versions(self) -> dict[str, str]:
        with self.sessions() as session:
            rows = session.scalars(select(AlgorithmConfigurationModel).where(
                AlgorithmConfigurationModel.status == "active"
            ))
            result = {row.config_type: row.version for row in rows}
        missing = CONFIG_TYPES - set(result)
        if missing:
            raise RuntimeError(f"active configurations missing: {sorted(missing)}")
        return result

    def payloads(self, versions: dict[str, str]) -> dict[str, dict]:
        with self.sessions() as session:
            rows = session.scalars(select(AlgorithmConfigurationModel).where(
                AlgorithmConfigurationModel.config_type.in_(versions),
            ))
            indexed = {(row.config_type, row.version): row.payload for row in rows}
        missing = [key for key, version in versions.items() if (key, version) not in indexed]
        if missing:
            raise ValueError(f"configuration versions not found: {missing}")
        return {key: indexed[(key, version)] for key, version in versions.items()}

    def transition(self, config_id: str, action: str, actor: str, note: str | None = None) -> dict[str, object] | None:
        with self.sessions.begin() as session:
            row = session.get(AlgorithmConfigurationModel, config_id)
            if row is None:
                return None
            now = utc_now()
            if action == "enable":
                if not note:
                    raise ValueError("human review note is required before enabling")
                if actor == row.created_by:
                    raise ValueError("configuration creator cannot approve their own version")
                active = session.scalars(select(AlgorithmConfigurationModel).where(
                    AlgorithmConfigurationModel.config_type == row.config_type,
                    AlgorithmConfigurationModel.status == "active",
                    AlgorithmConfigurationModel.id != row.id,
                ))
                for previous in active:
                    previous.status = "inactive"
                    previous.deactivated_at = now
                    session.add(AlgorithmConfigurationEventModel(
                        configuration_id=previous.id, action="superseded", actor=actor,
                        details={"replacement_id": row.id},
                    ))
                row.status = "active"
                row.reviewed_by = actor
                row.review_note = note
                row.activated_at = now
                row.deactivated_at = None
            elif action == "disable":
                row.status = "inactive"
                row.deactivated_at = now
            else:
                raise ValueError("unsupported configuration action")
            session.add(AlgorithmConfigurationEventModel(
                configuration_id=row.id, action=action, actor=actor, details={"note": note},
            ))
            return self._data(row)

    def history(self, config_id: str) -> list[dict[str, object]] | None:
        with self.sessions() as session:
            if session.get(AlgorithmConfigurationModel, config_id) is None:
                return None
            rows = session.scalars(select(AlgorithmConfigurationEventModel).where(
                AlgorithmConfigurationEventModel.configuration_id == config_id
            ).order_by(AlgorithmConfigurationEventModel.created_at, AlgorithmConfigurationEventModel.id))
            return [{
                "id": row.id, "configuration_id": row.configuration_id, "action": row.action,
                "actor": row.actor, "details": row.details, "created_at": row.created_at,
            } for row in rows]

    def compare(self, left_id: str, right_id: str) -> dict[str, object] | None:
        left, right = self.get_configuration(left_id), self.get_configuration(right_id)
        if left is None or right is None:
            return None
        left_payload, right_payload = dict(left["payload"]), dict(right["payload"])
        keys = sorted(set(left_payload) | set(right_payload))
        return {
            "left": {"id": left_id, "version": left["version"]},
            "right": {"id": right_id, "version": right["version"]},
            "changes": [{"key": key, "left": left_payload.get(key), "right": right_payload.get(key)}
                        for key in keys if left_payload.get(key) != right_payload.get(key)],
        }

    def eligible_snapshot_terms(self, cutoff: datetime) -> list[dict[str, object]]:
        with self.sessions() as session:
            rows = session.execute(
                select(SourceSnapshotModel, ExtractedTermModel)
                .join(ExtractedTermModel, ExtractedTermModel.snapshot_id == SourceSnapshotModel.id)
                .where(SourceSnapshotModel.published_at <= cutoff, SourceSnapshotModel.captured_at <= cutoff)
            )
            return [{
                "snapshot_id": snapshot.id, "source": snapshot.source,
                "published_at": snapshot.published_at, "captured_at": snapshot.captured_at,
                "term": term.term, "score": term.score,
            } for snapshot, term in rows]

    def create_backtest(self, request: dict[str, object], versions: dict[str, str]) -> tuple[dict[str, object], bool]:
        with self.sessions.begin() as session:
            query = select(BacktestRunModel).where(
                BacktestRunModel.request_id == request["request_id"]
            )
            if request.get("idempotency_key"):
                query = select(BacktestRunModel).where(
                    (BacktestRunModel.request_id == request["request_id"])
                    | (BacktestRunModel.idempotency_key == request["idempotency_key"])
                )
            existing = session.scalar(query)
            if existing:
                return self._backtest_data(existing), False
            row = BacktestRunModel(
                id=str(uuid4()), request_id=str(request["request_id"]),
                idempotency_key=request.get("idempotency_key"),
                status="pending", config_versions=versions, request_payload=request,
                dataset_id=str(request["dataset_id"]),
                dataset_version=str(request["dataset_version"]),
            )
            session.add(row)
            session.flush()
            return self._backtest_data(row), True

    @staticmethod
    def _backtest_data(row: BacktestRunModel) -> dict[str, object]:
        return {"id": row.id, "request_id": row.request_id, "idempotency_key": row.idempotency_key,
                "status": row.status,
                "config_versions": row.config_versions, "error_message": row.error_message,
                "dataset_id": row.dataset_id, "dataset_version": row.dataset_version,
                "created_at": row.created_at, "completed_at": row.completed_at}

    def save_backtest_results(self, run_id: str, slices: list[dict[str, object]]) -> None:
        with self.sessions.begin() as session:
            run = session.get(BacktestRunModel, run_id)
            if run is None or run.status == "succeeded":
                return
            run.status = "running"
            for value in slices:
                session.add(BacktestSliceResultModel(backtest_run_id=run_id, **value))
            run.status = "succeeded"
            run.completed_at = utc_now()

    def get_backtest(self, run_id: str) -> dict[str, object] | None:
        with self.sessions() as session:
            row = session.get(BacktestRunModel, run_id)
            return self._backtest_data(row) if row else None

    def backtest_results(self, run_id: str) -> list[dict[str, object]] | None:
        with self.sessions() as session:
            if session.get(BacktestRunModel, run_id) is None:
                return None
            rows = session.scalars(select(BacktestSliceResultModel).where(
                BacktestSliceResultModel.backtest_run_id == run_id
            ).order_by(BacktestSliceResultModel.observation_cutoff))
            return [{
                "slice_key": row.slice_key, "observation_cutoff": row.observation_cutoff,
                "validation_end": row.validation_end, "predictions": row.predictions,
                "ground_truth": row.ground_truth, "metrics": row.metrics,
                "source_ablation": row.ablation_results, "stability": row.stability_results,
                "quality_flags": row.quality_flags,
            } for row in rows]
