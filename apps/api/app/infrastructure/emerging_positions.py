from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.platform import DEFAULT_CONFIGS
from app.domain.emerging_position import EmergingCandidate, GerminationAssessment
from app.domain.values import freeze, thaw
from app.models.emerging_definition_version import EmergingDefinitionVersion
from app.models.emerging_position import EmergingPosition
from app.models.position_cluster import PositionCluster
from app.models.standard_position import StandardPosition
from app.models.system_config import SystemConfig
from app.contexts.emerging_positions import (
    ClusterRecord,
    DefinitionVersionRecord,
    DuplicateEmergingProjection,
    EmergingRecord,
    ReleaseGateConfig,
    StandardPositionRecord,
)


class SqlAlchemyEmergingPositionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_cluster(self, cluster_id: str) -> ClusterRecord | None:
        row = self._session.get(PositionCluster, cluster_id)
        if row is None:
            return None
        return ClusterRecord(
            cluster_id=row.id,
            cluster_name=row.cluster_name,
            core_skills=tuple(freeze(item) for item in (row.core_skills or [])),
            representative_jd_ids=tuple(row.representative_jd_ids or []),
            stability_score=row.stability_score,
            discovery_run_id=row.discovery_run_id,
            discovery_run_status=row.discovery_run_status,
            assessment=GerminationAssessment.from_values(
                row.discovery_assessment or {}, row.discovery_run_id
            ),
            generated_definition=freeze(row.generated_definition or {}),
        )

    def upsert_formal_experiment_cluster(
        self,
        *,
        cluster_id: str,
        cluster_name: str,
        sample_count: int,
        representative_titles: tuple[str, ...],
        representative_jd_ids: tuple[str, ...],
        discovery_assessment: Mapping[str, object],
        generated_definition: Mapping[str, object],
        discovery_run_id: str,
    ) -> bool:
        row = self._session.get(PositionCluster, cluster_id)
        if row is not None:
            row.cluster_name = cluster_name
            row.sample_count = sample_count
            row.representative_titles = list(representative_titles)
            row.representative_jd_ids = list(representative_jd_ids)
            row.discovery_assessment = dict(discovery_assessment)
            row.generated_definition = dict(generated_definition)
            row.discovery_run_status = "succeeded"
            return False
        self._session.add(
            PositionCluster(
                id=cluster_id,
                cluster_name=cluster_name,
                algorithm="emerge-v3.2:emerge_v3_2",
                sample_count=sample_count,
                core_skills=[],
                representative_titles=list(representative_titles),
                representative_jd_ids=list(representative_jd_ids),
                stability_score=1.0,
                growth_score=0.0,
                distance_from_existing_positions=1.0,
                discovery_run_id=discovery_run_id,
                discovery_run_status="succeeded",
                discovery_assessment=dict(discovery_assessment),
                generated_definition=dict(generated_definition),
                discovery_lineages=[],
                status="active",
            )
        )
        # emerging_positions.cluster_id references position_clusters.id, but the
        # two mappers have no ORM relationship, so SQLAlchemy cannot infer the
        # insert order. Flush the parent row before any candidate insert.
        self._session.flush()
        return True

    def get(self, emerging_id: str) -> EmergingRecord | None:
        row = self._session.get(EmergingPosition, emerging_id)
        return self._record(row) if row is not None else None

    def get_by_cluster(self, cluster_id: str) -> EmergingRecord | None:
        row = (
            self._session.query(EmergingPosition)
            .filter(EmergingPosition.cluster_id == cluster_id)
            .first()
        )
        return self._record(row) if row is not None else None

    def list(self) -> list[EmergingRecord]:
        rows = self._session.query(EmergingPosition).order_by(EmergingPosition.created_at.desc()).all()
        return [self._record(row) for row in rows]

    def add_candidate(self, candidate: EmergingCandidate) -> None:
        self._session.add(self._new_model(candidate))

    def save_candidate(self, candidate: EmergingCandidate) -> None:
        row = self._session.get(EmergingPosition, candidate.candidate_id)
        if row is None:
            raise LookupError(candidate.candidate_id)
        row.position_name = candidate.position_name
        row.core_responsibilities = thaw(candidate.core_responsibilities)
        row.required_skills = thaw(candidate.required_skills)
        row.bonus_skills = thaw(candidate.bonus_skills)
        row.industry_scenarios = thaw(candidate.industry_scenarios)
        row.status = candidate.status.value
        row.field_evidence = thaw(candidate.field_evidence)
        row.review_history = thaw(candidate.review_history)
        row.published_snapshot = thaw(candidate.published_snapshot) or None
        # Algorithm score, dimensions and evidence are projections owned by
        # discovery and intentionally cannot be updated through this method.

    def delete_candidate(self, emerging_id: str) -> None:
        row = self._session.get(EmergingPosition, emerging_id)
        if row is not None:
            self._session.delete(row)

    def release_config(self) -> ReleaseGateConfig:
        row = self._session.get(SystemConfig, "germination-score")
        config = deepcopy(row.config if row else DEFAULT_CONFIGS["germination-score"])
        return ReleaseGateConfig(
            float(config.get("minimum_stability_score", 0.65)),
            float(config.get("emerging_threshold", 0.6)),
        )

    def get_standard_by_source(self, emerging_id: str) -> StandardPositionRecord | None:
        row = (
            self._session.query(StandardPosition)
            .filter(StandardPosition.source_emerging_position_id == emerging_id)
            .first()
        )
        return self._standard_record(row) if row is not None else None

    def add_standard_from(self, candidate: EmergingCandidate) -> StandardPositionRecord:
        row = StandardPosition(
            position_name=candidate.position_name,
            source_emerging_position_id=candidate.candidate_id,
            core_responsibilities=thaw(candidate.core_responsibilities),
            required_skills=thaw(candidate.required_skills),
            bonus_skills=thaw(candidate.bonus_skills),
            industry_scenarios=list(candidate.industry_scenarios),
            status="existing",
            graph_onboarding_status="mapping_required",
        )
        self._session.add(row)
        self._session.flush()
        return self._standard_record(row)

    def create_definition_version(
        self, candidate: EmergingCandidate, actor_id: str
    ) -> DefinitionVersionRecord:
        # The candidate and version mappers have no relationship to order inserts.
        # Persist a newly added parent before the version's foreign key is flushed.
        self._session.flush()
        self._session.query(EmergingDefinitionVersion).filter(
            EmergingDefinitionVersion.emerging_id == candidate.candidate_id
        ).update({EmergingDefinitionVersion.selected: False}, synchronize_session=False)
        version = EmergingDefinitionVersion(
            emerging_id=candidate.candidate_id,
            snapshot={
                "position_name": candidate.position_name,
                "core_responsibilities": list(candidate.core_responsibilities),
                "required_skills": thaw(candidate.required_skills),
                "bonus_skills": thaw(candidate.bonus_skills),
                "industry_scenarios": list(candidate.industry_scenarios),
                "evidence_jd_ids": list(candidate.evidence_jd_ids),
                "field_evidence": thaw(candidate.field_evidence),
            },
            selected=True,
            created_by=actor_id,
        )
        self._session.add(version)
        self._session.flush()
        return self._version_record(version)

    def list_definition_versions(self, emerging_id: str) -> list[DefinitionVersionRecord]:
        rows = (
            self._session.query(EmergingDefinitionVersion)
            .filter(EmergingDefinitionVersion.emerging_id == emerging_id)
            .order_by(EmergingDefinitionVersion.created_at.asc(), EmergingDefinitionVersion.id.asc())
            .all()
        )
        return [self._version_record(row) for row in rows]

    def select_definition_version(
        self, emerging_id: str, version_id: str
    ) -> tuple[EmergingCandidate, DefinitionVersionRecord] | None:
        version = (
            self._session.query(EmergingDefinitionVersion)
            .filter(
                EmergingDefinitionVersion.id == version_id,
                EmergingDefinitionVersion.emerging_id == emerging_id,
            )
            .first()
        )
        row = self._session.get(EmergingPosition, emerging_id)
        if version is None or row is None:
            return None
        snapshot = version.snapshot or {}
        # Evidence is an immutable discovery projection. Historical snapshots
        # may contain it, but selecting a definition never overwrites it.
        for field in (
            "position_name",
            "core_responsibilities",
            "required_skills",
            "bonus_skills",
            "industry_scenarios",
            "field_evidence",
        ):
            if field in snapshot:
                setattr(row, field, snapshot[field])
        self._session.query(EmergingDefinitionVersion).filter(
            EmergingDefinitionVersion.emerging_id == emerging_id
        ).update({EmergingDefinitionVersion.selected: False}, synchronize_session=False)
        version.selected = True
        candidate = self._candidate(row)
        return candidate, self._version_record(version)

    @staticmethod
    def _candidate(row: EmergingPosition) -> EmergingCandidate:
        return EmergingCandidate.create(
            candidate_id=row.id,
            cluster_id=row.cluster_id,
            position_name=row.position_name,
            core_responsibilities=row.core_responsibilities or [],
            required_skills=row.required_skills or [],
            bonus_skills=row.bonus_skills or [],
            industry_scenarios=row.industry_scenarios or [],
            germination_score=row.germination_score,
            score_dimensions=row.score_dimensions or {},
            evidence_jd_ids=row.evidence_jd_ids or [],
            status=row.status,
            field_evidence=row.field_evidence or {},
            review_history=row.review_history or [],
            published_snapshot=row.published_snapshot or {},
        )

    def _record(self, row: EmergingPosition) -> EmergingRecord:
        return EmergingRecord(
            self._candidate(row),
            row.created_at,
            row.updated_at,
            self.get_standard_by_source(row.id),
        )

    @staticmethod
    def _new_model(candidate: EmergingCandidate) -> EmergingPosition:
        return EmergingPosition(
            id=candidate.candidate_id,
            cluster_id=candidate.cluster_id,
            position_name=candidate.position_name,
            core_responsibilities=list(candidate.core_responsibilities),
            required_skills=thaw(candidate.required_skills),
            bonus_skills=thaw(candidate.bonus_skills),
            industry_scenarios=list(candidate.industry_scenarios),
            germination_score=candidate.germination_score,
            score_dimensions=thaw(candidate.score_dimensions),
            evidence_jd_ids=list(candidate.evidence_jd_ids),
            status=candidate.status.value,
            field_evidence=thaw(candidate.field_evidence),
            review_history=thaw(candidate.review_history),
            published_snapshot=thaw(candidate.published_snapshot) or None,
        )

    @staticmethod
    def _standard_record(row: StandardPosition) -> StandardPositionRecord:
        return StandardPositionRecord(
            standard_position_id=row.id,
            position_name=row.position_name,
            source_emerging_position_id=row.source_emerging_position_id,
            status=row.status,
            required_skills=tuple(freeze(item) for item in (row.required_skills or [])),
            created_at=row.created_at,
            graph_onboarding_status=row.graph_onboarding_status,
        )

    @staticmethod
    def _version_record(row: EmergingDefinitionVersion) -> DefinitionVersionRecord:
        return DefinitionVersionRecord(
            version_id=row.id,
            emerging_id=row.emerging_id,
            snapshot=freeze(row.snapshot or {}),
            selected=bool(row.selected),
            created_by=row.created_by,
            created_at=row.created_at,
        )


class SqlAlchemyEmergingPositionUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyEmergingPositionUnitOfWork":
        self._session = self._session_factory()
        self.repository = SqlAlchemyEmergingPositionRepository(self._session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            self.rollback()
        self._session.close()

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            message = str(exc.orig).lower()
            if "emerging_positions.cluster_id" in message or "standard_positions.source_emerging_position_id" in message or "uq_emerging" in message or "uq_standard" in message:
                raise DuplicateEmergingProjection(str(exc)) from exc
            raise

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
