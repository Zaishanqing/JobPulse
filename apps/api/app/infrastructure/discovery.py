"""SQLAlchemy and HTTP adapters for the position-discovery use case."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.contexts.platform import DEFAULT_CONFIGS
from app.integrations.emerging_discovery.client import EmergingDiscoveryClient
from app.integrations.emerging_discovery.exceptions import EmergingDiscoveryError
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult
from app.models.jd_publication import JDPublication
from app.models.position_cluster import PositionCluster
from app.models.source_jd import SourceJD, SourceJDVersion
from app.models.task_record import TaskRecord as TaskRow
from app.models.system_config import SystemConfig
from app.contexts.discovery import (
    CandidateObservation,
    CandidateDiffusionGraph,
    CandidateTrajectory,
    ClusterJDRecord,
    ClusterProjection,
    DiscoveryCandidate,
    DiscoveryCandidateDetail,
    DiscoveryClusterResult,
    DiscoveryRunRequest,
    DiscoveryRunResult,
    FROZEN_DISCOVERY_DATASET_ID,
    ReleasedJDFact,
    released_jd_contract,
)
from app.contexts.tasks import TaskLog, TaskPayload, TaskRecord
from app.domain.values import freeze, thaw
from app.domain.errors import ExternalGatewayError
from app.domain.text_cleaning import clean_jd_text_for_display
from app.infrastructure.discovery_datasets import (
    frozen_cluster_jds,
    frozen_discovery_windows,
    list_frozen_discovery_facts,
)


class SqlAlchemyDiscoveryRepository:
    def __init__(self, session: Session, *, allow_legacy_reviewed: bool = True) -> None:
        self._session = session
        self._allow_legacy_reviewed = allow_legacy_reviewed

    def list_released_jd_facts(self) -> list[ReleasedJDFact]:
        publications = (
            self._session.query(JobDescription, JDPublication, SourceJDVersion, SourceJD)
            .join(JDPublication, JDPublication.jd_id == JobDescription.id)
            .outerjoin(
                SourceJDVersion,
                SourceJDVersion.id == JDPublication.source_jd_version_id,
            )
            .outerjoin(SourceJD, SourceJD.id == JDPublication.source_jd_id)
            .order_by(JDPublication.created_at.asc(), JDPublication.id.asc())
            .all()
        )
        facts = [
            self._publication_fact(jd, publication, source_version, source_jd)
            for jd, publication, source_version, source_jd in publications
        ]
        if not self._allow_legacy_reviewed:
            return facts
        published_jd_ids = {publication.jd_id for _, publication, _, _ in publications}
        rows = (
            self._session.query(JobDescription, JDParseResult)
            .join(JDParseResult, JDParseResult.jd_id == JobDescription.id)
            .filter(
                JDParseResult.workflow_status.in_(("reviewed", "published")),
                JDParseResult.need_review.is_(False),
                JDParseResult.schema_version == "v2",
                ~JobDescription.id.in_(published_jd_ids),
            )
            .order_by(JobDescription.created_at.asc())
            .all()
        )
        facts.extend(
            ReleasedJDFact(
                source_fact_id=f"legacy-reviewed:{result.id}",
                source_fact_version=result.updated_at.isoformat(),
                jd_id=jd.id,
                title=jd.title,
                source_name=jd.source_name or jd.source_type or jd.enterprise_id,
                publish_date=jd.publish_date,
                structured_data={
                    "position_title": result.position_title,
                    "responsibilities": result.responsibilities or [],
                    "required_skills": result.required_skills or [],
                    "bonus_skills": result.bonus_skills or [],
                    "industry": result.industry,
                    "business_scenarios": result.business_scenarios or [],
                },
                schema_version=result.schema_version,
                review_status=result.workflow_status,
                consumption_path=(
                    "published"
                    if result.workflow_status == "published"
                    else "legacy_reviewed"
                ),
            )
            for jd, result in rows
        )
        return facts

    def list_dataset_jd_facts(self, dataset_id: str) -> list[ReleasedJDFact]:
        try:
            return list_frozen_discovery_facts(dataset_id)
        except (LookupError, OSError, ValueError):
            return []

    def dataset_time_windows(self, dataset_id: str):
        try:
            if dataset_id != FROZEN_DISCOVERY_DATASET_ID:
                return None
            return frozen_discovery_windows()
        except (OSError, ValueError):
            return None

    @staticmethod
    def _published_company_name(snapshot: dict[str, Any]) -> str | None:
        extraction = snapshot.get("extraction_result")
        if not isinstance(extraction, dict):
            return None
        facts = extraction.get("company_facts")
        if not isinstance(facts, list):
            return None
        for fact in facts:
            if not isinstance(fact, dict) or fact.get("kind") != "company_name":
                continue
            value = str(fact.get("value") or "").strip()
            if value:
                return value
        return None

    @staticmethod
    def _source_platform(source_name: str | None) -> str | None:
        if not source_name:
            return None
        parts = source_name.split(":", 2)
        if len(parts) == 3 and parts[0] == "emerging-discovery-3day-v1":
            return parts[1].strip().casefold() or None
        return None

    @staticmethod
    def _publication_fact(
        jd: JobDescription,
        publication: JDPublication,
        source_version: SourceJDVersion | None,
        source_jd: SourceJD | None = None,
    ) -> ReleasedJDFact:
        if publication.source_jd_version_id is not None and source_version is None:
            raise ValueError("Published JD references a missing SourceJDVersion")
        snapshot = publication.snapshot_payload
        legacy = snapshot["legacy"]
        jd_snapshot = snapshot["jd"]
        company_name = SqlAlchemyDiscoveryRepository._published_company_name(snapshot)
        source_name = (
            jd.source_name
            or jd_snapshot.get("source_name")
            or jd_snapshot.get("source_type")
            or jd_snapshot.get("enterprise_id")
        )
        source_platform = (
            source_jd.source_platform
            if source_jd is not None
            else SqlAlchemyDiscoveryRepository._source_platform(source_name)
        )
        published_date = (
            date.fromisoformat(str(jd_snapshot["publish_date"]))
            if jd_snapshot.get("publish_date")
            else jd.publish_date
        )
        raw_payload = getattr(source_version, "raw_payload", {}) if source_version is not None else {}
        return ReleasedJDFact(
            source_fact_id=publication.id,
            source_fact_version=publication.created_at.isoformat(),
            jd_id=publication.jd_id,
            title=jd_snapshot["title"],
            source_name=source_name,
            # SourceJDVersion.crawl_time is ingestion metadata in this model.
            # Formal temporal evidence only accepts the JD's published event.
            publish_date=published_date,
            structured_data={
                "position_title": legacy["position_title"],
                "responsibilities": legacy["responsibilities"],
                "required_skills": legacy["required_skills"],
                "bonus_skills": legacy["bonus_skills"],
                "industry": legacy["industry"],
                "business_scenarios": legacy["business_scenarios"],
                **({"company_name": company_name} if company_name else {}),
                **({"source_platform": source_platform} if source_platform else {}),
            },
            content_hash=(getattr(source_version, "content_hash", None) if source_version is not None else None),
            source_record_id=(
                source_jd.source_record_id if source_jd is not None else None
            ),
            bundle_id=(
                str(raw_payload.get("bundle_id")) if raw_payload.get("bundle_id") else None
            ),
            date_source="publish_date" if published_date is not None else None,
            schema_version=publication.schema_version,
            review_status="published",
            consumption_path="published",
        )

    def discovery_config(self) -> dict[str, Any]:
        record = self._session.get(SystemConfig, "germination-score")
        defaults = deepcopy(DEFAULT_CONFIGS["germination-score"])
        if record is None:
            return defaults
        merged = {**defaults, **deepcopy(record.config or {})}
        # These describe the executable contract, not an operator-tunable
        # threshold. Persisted pre-v4 values must not silently re-enable the
        # semantic fallback or mislabel newly computed results.
        for key in (
            "formula_version",
            "semantic_failure_mode",
            "formal_algorithm_version",
            "candidate_clustering_version",
            "stage2_unit",
            "split_refinement_enabled",
        ):
            merged[key] = defaults[key]
        return merged

    def get_cluster(self, cluster_id: str) -> ClusterProjection | None:
        row = self._session.get(PositionCluster, cluster_id)
        if row is None:
            return None
        return self._to_projection(row)

    def list_clusters(self) -> list[ClusterProjection]:
        rows = self._session.query(PositionCluster).order_by(
            PositionCluster.created_at.desc()
        ).all()
        return [self._to_projection(row) for row in rows]

    def cluster_jds(self, cluster_id: str) -> list[ClusterJDRecord]:
        cluster = self._session.get(PositionCluster, cluster_id)
        if cluster is None or not cluster.representative_jd_ids:
            return []
        ids = list(cluster.representative_jd_ids)
        rows = self._session.query(JobDescription).filter(JobDescription.id.in_(ids)).all()
        by_id = {row.id: row for row in rows}
        records = [self._serialize_jd(by_id[jd_id]) for jd_id in ids if jd_id in by_id]
        missing = [jd_id for jd_id in ids if jd_id not in by_id]
        virtual_by_id = {row.jd_id: row for row in frozen_cluster_jds(missing)}
        combined = {row.jd_id: row for row in records}
        combined.update(virtual_by_id)
        return [combined[jd_id] for jd_id in ids if jd_id in combined]

    def delete_cluster(self, cluster_id: str) -> None:
        row = self._session.get(PositionCluster, cluster_id)
        if row is not None:
            self._session.delete(row)

    def add_cluster(self, projection: ClusterProjection) -> None:
        self._session.add(
            PositionCluster(
                id=projection.cluster_id,
                cluster_name=projection.cluster_name,
                algorithm=projection.algorithm_version,
                time_window_start=projection.time_window_start,
                time_window_end=projection.time_window_end,
                sample_count=projection.sample_count,
                core_skills=thaw(projection.core_skills),
                representative_titles=thaw(projection.representative_titles),
                representative_jd_ids=thaw(projection.representative_jd_ids),
                stability_score=projection.stability_score,
                growth_score=projection.growth_score,
                distance_from_existing_positions=(
                    projection.distance_from_existing_positions
                ),
                discovery_run_id=projection.discovery_run_id,
                discovery_run_status=projection.discovery_run_status,
                discovery_assessment=thaw(projection.discovery_assessment),
                generated_definition=thaw(projection.generated_definition),
                discovery_lineages=thaw(projection.discovery_lineages),
                status="active",
            )
        )

    def get_task(self, task_id: str) -> TaskRecord | None:
        task = self._session.get(TaskRow, task_id)
        return self._to_task(task) if task is not None else None

    def record_succeeded_task(
        self,
        *,
        actor_id: str,
        input_payload: TaskPayload,
        result_payload: TaskPayload,
        task_id: str,
    ) -> TaskRecord:
        now = datetime.now(timezone.utc)
        truthful_result = {
            "implementation_status": "remote_vector_discovery_service",
            "provider": "emerging_discovery_http",
            "algorithm_version": "emerge-v3.2",
            "mock": False,
            "rule_based": False,
            **thaw(result_payload.values),
        }
        task = TaskRow(
            id=task_id,
            task_type="position_cluster",
            status="succeeded",
            progress=1.0,
            input_payload=thaw(input_payload.values),
            result_payload=truthful_result,
            created_by=actor_id,
            log_entries=[
                {"status": "pending", "at": now.isoformat()},
                {"status": "running", "at": now.isoformat()},
                {
                    "status": "succeeded",
                    "at": now.isoformat(),
                    "message": "Completed by synchronous local executor",
                },
            ],
            started_at=now,
            finished_at=now,
        )
        self._session.add(task)
        # Keep the uniqueness race at the UoW commit boundary.  The application
        # owns rollback/reload semantics and can return the concurrent winner.
        return self._to_task(task)

    @staticmethod
    def _to_task(task: TaskRow) -> TaskRecord:
        return TaskRecord(
            task.id,
            task.task_type,
            task.status,
            task.progress,
            TaskPayload.from_mapping(task.input_payload),
            TaskPayload.from_mapping(task.result_payload),
            task.result_reference,
            task.error_code,
            task.error_message,
            task.created_by,
            task.attempt_count,
            tuple(
                TaskLog(item["status"], item["at"], item.get("message"))
                for item in (task.log_entries or [])
            ),
            task.created_at,
            task.updated_at,
            task.started_at,
            task.finished_at,
        )

    @staticmethod
    def _to_projection(row: PositionCluster) -> ClusterProjection:
        return ClusterProjection(
            cluster_id=row.id,
            discovery_run_id=row.discovery_run_id,
            cluster_name=row.cluster_name,
            algorithm_version=row.algorithm,
            sample_count=row.sample_count,
            core_skills=row.core_skills or [],
            representative_titles=row.representative_titles or [],
            representative_jd_ids=row.representative_jd_ids or [],
            stability_score=row.stability_score,
            growth_score=row.growth_score,
            distance_from_existing_positions=row.distance_from_existing_positions,
            discovery_run_status=row.discovery_run_status,
            discovery_assessment=row.discovery_assessment or {},
            generated_definition=row.generated_definition or {},
            discovery_lineages=row.discovery_lineages or [],
            time_window_start=row.time_window_start,
            time_window_end=row.time_window_end,
            status=row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _serialize_jd(row: JobDescription) -> ClusterJDRecord:
        return ClusterJDRecord(
            row.id,
            row.source_type,
            row.source_name,
            row.enterprise_id,
            row.title,
            row.cleaned_text or clean_jd_text_for_display(row.raw_text),
            row.publish_date,
            row.url,
            row.file_id,
            row.parse_status,
            row.input_extraction_status,
            row.input_provider,
            row.input_error_code,
            row.input_error_message,
            row.copy_risk_score,
            row.inflation_score,
            row.is_downweighted,
            row.created_at,
            row.updated_at,
        )


class SqlAlchemyDiscoveryUnitOfWork:
    def __init__(
        self,
        source: Session | sessionmaker[Session],
        *,
        allow_legacy_reviewed: bool = True,
    ) -> None:
        self._source = source
        self._session: Session | None = None
        self._owns_session = not isinstance(source, Session)
        self._allow_legacy_reviewed = allow_legacy_reviewed

    def __enter__(self) -> "SqlAlchemyDiscoveryUnitOfWork":
        self._session = self._source() if self._owns_session else self._source
        self.repository = SqlAlchemyDiscoveryRepository(
            self._session,
            allow_legacy_reviewed=self._allow_legacy_reviewed,
        )
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is not None:
            self.rollback()
        if self._owns_session and self._session is not None:
            self._session.close()

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()


def discovery_run_result(result: dict[str, Any]) -> DiscoveryRunResult:
    return DiscoveryRunResult(
        run_id=result["run_id"],
        status=result["status"],
        input_quality_report=freeze(result.get("input_quality_report", {})),
        algorithm_version=result["algorithm_version"],
        clusters=tuple(
            DiscoveryClusterResult(
                cluster_id=item["cluster_id"],
                cluster_name=item["cluster_name"],
                sample_count=item["sample_count"],
                core_skills=tuple(freeze(skill) for skill in item.get("core_skills", [])),
                representative_titles=tuple(item.get("representative_titles", [])),
                representative_jd_ids=tuple(item.get("representative_jd_ids", [])),
                stability_score=item["stability_score"],
                growth_score=item["growth_score"],
                distance_from_existing_positions=item["distance_from_existing_positions"],
                emergence_assessment=freeze(item.get("emergence_assessment", {})),
                generated_definition=freeze(item.get("generated_definition", {})),
                standard_position_comparison=freeze(
                    item.get("standard_position_comparison", {})
                ),
                explainability=freeze(item.get("explainability", {})),
                lineage_relations=tuple(
                    freeze(relation) for relation in item.get("lineage_relations", [])
                ),
            )
            for item in result.get("clusters", [])
        ),
        lineages=tuple(freeze(item) for item in result.get("lineages", [])),
        request_id=str(result.get("request_id", "")),
        input_fingerprint=(
            str(result["input_fingerprint"])
            if result.get("input_fingerprint") is not None
            else None
        ),
        run_context=freeze(result.get("run_context", {})),
        provider=result.get("provider", "emerging_discovery_http"),
        implementation_status=result.get("implementation_status", "remote_vector_discovery_service"),
        mock=bool(result.get("mock", False)),
        rule_based=bool(result.get("rule_based", False)),
    )


def _string(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def discovery_candidate_data(raw: dict[str, Any]) -> DiscoveryCandidate:
    """Map the real upstream candidate.v1 payload into the main-system DTO.

    Only fields provided by upstream are preserved; unknown fields are trimmed.
    """
    return DiscoveryCandidate(
        candidate_id=_string(raw.get("candidate_id")),
        status=_string(raw.get("status")),
        first_seen_window_id=_string(raw.get("first_seen_window_id")),
        last_seen_window_id=_string(raw.get("last_seen_window_id")),
        age=_int(raw.get("age")),
        current_cluster_id=(
            str(raw["current_cluster_id"])
            if raw.get("current_cluster_id") is not None
            else None
        ),
        previous_cluster_ids=tuple(
            str(item) for item in (raw.get("previous_cluster_ids") or [])
        ),
        canonical_title=_string(raw.get("canonical_title")),
        display_title=_string(raw.get("display_title")),
        definition=freeze(raw.get("definition") or {}),
        identity_profile=freeze(raw.get("identity_profile") or {}),
        evidence=freeze(raw.get("evidence") or {}),
        support_count=_int(raw.get("support_count")),
        company_coverage=_int(raw.get("company_coverage")),
        skill_similarity=_optional_float(raw.get("skill_similarity")),
        responsibility_similarity=_optional_float(raw.get("responsibility_similarity")),
        title_similarity=_optional_float(raw.get("title_similarity")),
        membership_overlap=_optional_float(raw.get("membership_overlap")),
        identity_similarity=_float(raw.get("identity_similarity")),
        novelty_score=_float(raw.get("novelty_score")),
        emergence_score=_float(raw.get("emergence_score")),
        identity_stability=_int(raw.get("identity_stability")),
        created_at=raw.get("created_at") if raw.get("created_at") is not None else None,
        updated_at=raw.get("updated_at") if raw.get("updated_at") is not None else None,
    )


def discovery_observation_data(raw: dict[str, Any]) -> CandidateObservation:
    return CandidateObservation(
        observation_id=_string(raw.get("observation_id")),
        candidate_id=_string(raw.get("candidate_id")),
        run_id=_string(raw.get("run_id")),
        cluster_id=_string(raw.get("cluster_id")),
        window_id=_string(raw.get("window_id")),
        title=_string(raw.get("title")),
        status=_string(raw.get("status")),
        emergence_score=_float(raw.get("emergence_score")),
        support_count=_int(raw.get("support_count")),
        company_count=_int(raw.get("company_count")),
        identity_similarity=_float(raw.get("identity_similarity")),
        skill_similarity=_optional_float(raw.get("skill_similarity")),
        responsibility_similarity=_optional_float(raw.get("responsibility_similarity")),
        title_similarity=_optional_float(raw.get("title_similarity")),
        membership_overlap=_optional_float(raw.get("membership_overlap")),
        semantic_similarity=_optional_float(raw.get("semantic_similarity")),
        cluster_name=(
            str(raw["cluster_name"])
            if raw.get("cluster_name") is not None
            else None
        ),
        evidence=freeze(raw.get("evidence") or {}),
        match_evidence=freeze(raw.get("match_evidence") or {}),
        created_at=raw.get("created_at") if raw.get("created_at") is not None else None,
    )


def discovery_candidate_detail_data(raw: dict[str, Any]) -> DiscoveryCandidateDetail:
    latest = raw.get("latest_observation")
    return DiscoveryCandidateDetail(
        candidate=discovery_candidate_data(raw.get("candidate") or {}),
        latest_observation=(
            discovery_observation_data(latest)
            if isinstance(latest, dict)
            else None
        ),
    )


def discovery_candidate_trajectory_data(raw: dict[str, Any]) -> CandidateTrajectory:
    return CandidateTrajectory(
        candidate_id=_string(raw.get("candidate_id")),
        trajectory=tuple(
            discovery_observation_data(item)
            for item in (raw.get("trajectory") or [])
            if isinstance(item, dict)
        ),
    )


class HttpEmergingDiscoveryGateway:
    def __init__(self, client: EmergingDiscoveryClient | None = None) -> None:
        self._client = client or EmergingDiscoveryClient()

    def create_run(self, request: DiscoveryRunRequest) -> DiscoveryRunResult:
        payload = discovery_run_payload(request)
        try:
            # POST is idempotent by the caller-provided request id.
            result = self._client.create_run(payload)
            return discovery_run_result(result)
        except EmergingDiscoveryError as exc:
            raise ExternalGatewayError(
                str(exc),
                status_code=exc.status_code,
                error_code=exc.error_code,
                details=exc.details,
            ) from exc

    def list_candidates(
        self,
        *,
        status: str | None = None,
        candidate_id: str | None = None,
        window_id: str | None = None,
    ) -> tuple[DiscoveryCandidate, ...]:
        try:
            result = self._client.list_candidates(
                status=status,
                candidate_id=candidate_id,
                window_id=window_id,
            )
        except EmergingDiscoveryError as exc:
            raise ExternalGatewayError(
                str(exc),
                status_code=exc.status_code,
                error_code=exc.error_code,
                details=exc.details,
            ) from exc
        return tuple(
            discovery_candidate_data(item)
            for item in result.get("candidates", [])
            if isinstance(item, dict)
        )

    def get_candidate(self, candidate_id: str) -> DiscoveryCandidateDetail:
        try:
            result = self._client.get_candidate(candidate_id)
        except EmergingDiscoveryError as exc:
            raise ExternalGatewayError(
                str(exc),
                status_code=exc.status_code,
                error_code=exc.error_code,
                details=exc.details,
            ) from exc
        return discovery_candidate_detail_data(result)

    def get_candidate_trajectory(self, candidate_id: str) -> CandidateTrajectory:
        try:
            result = self._client.get_candidate_trajectory(candidate_id)
        except EmergingDiscoveryError as exc:
            raise ExternalGatewayError(
                str(exc),
                status_code=exc.status_code,
                error_code=exc.error_code,
                details=exc.details,
            ) from exc
        return discovery_candidate_trajectory_data(result)

    def get_candidate_diffusion(self, candidate_id: str) -> CandidateDiffusionGraph:
        try:
            result = self._client.get_candidate_diffusion(candidate_id)
        except EmergingDiscoveryError as exc:
            raise ExternalGatewayError(
                str(exc),
                status_code=exc.status_code,
                error_code=exc.error_code,
                details=exc.details,
            ) from exc
        return CandidateDiffusionGraph(candidate_id=candidate_id, graph=freeze(result))


def discovery_run_payload(request: DiscoveryRunRequest) -> dict[str, object]:
    """Map the typed provider contract to the discovery.v2 HTTP JSON payload."""
    if request.time_window_start is None or request.time_window_end is None:
        raise ValueError("discovery requires an explicit historical time range")
    if len(request.time_windows) < 3:
        raise ValueError("discovery requires at least three historical windows")
    return {
        "contract_version": request.contract_version,
        "request_id": request.request_id,
        "algorithm": request.algorithm,
        "time_windows": [
            {
                "window_id": window.window_id,
                "start": window.start.isoformat(),
                "end": window.end.isoformat(),
            }
            for window in request.time_windows
        ],
        "current_observation_window_id": request.current_observation_window_id,
        "snapshots": [released_jd_contract(fact) for fact in request.snapshots],
        "config": thaw(request.config),
    }
