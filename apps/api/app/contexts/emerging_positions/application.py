from __future__ import annotations

from dataclasses import dataclass as _dataclass, replace as _replace
from datetime import datetime as _datetime, timezone as _timezone
from typing import Callable as _Callable
from uuid import NAMESPACE_URL as _UUID_NAMESPACE
from uuid import uuid4 as _uuid4
from uuid import uuid5 as _uuid5

from app.domain.emerging_position import (
    EmergingCandidate,
    EmergingPositionStatus,
    GerminationAssessment as _GerminationAssessment,
    InvalidEmergingTransition,
    ReleaseGateRejected,
    ReleaseGateEvidence,
)
from app.contexts.emerging_positions.contracts import (
    ClusterRecord,
    DefinitionSelectionRecord,
    DefinitionVersionRecord,
    EmergingActor,
    EmergingChanges,
    EmergingRecord,
    GeneratedDefinitionRecord,
    GerminationAssessmentRecord,
    ReviewEmergingDefinitionCommand,
    ReleaseGateConfig,
    StandardPositionRecord,
)
from app.contexts.emerging_positions.ports import (
    DuplicateEmergingProjection,
    EmergingPositionUnitOfWork,
)
from app.domain.errors import PermissionDenied
from app.domain.values import freeze as _freeze, thaw as _thaw


_ADMIN_ROLES = {"admin"}
_UoWFactory = _Callable[[], EmergingPositionUnitOfWork]
_FORMAL_EXPERIMENT_ID = "EXP-EMERGE-01-CROSSWINDOW-V3.2-20260823"
_FORMAL_DISCOVERY_RUN_ID = "formal-exp-emerge-01-v3.2-20260823"


class EmergingPositionNotFound(LookupError):
    pass


class EmergingClusterNotFound(LookupError):
    pass


class DefinitionVersionNotFound(LookupError):
    pass


class DiscoveryEvidenceUnavailable(RuntimeError):
    pass


@_dataclass(frozen=True)
class FormalExperimentImportRecord:
    experiment_id: str
    imported: int
    existing: int
    cluster_keys: tuple[str, ...]


def _require_admin(actor: EmergingActor) -> None:
    if actor.role not in _ADMIN_ROLES:
        raise PermissionDenied("No permission to manage emerging positions")


def _effective_assessment(
    cluster: ClusterRecord,
    candidate: EmergingCandidate,
    config: ReleaseGateConfig,
) -> tuple[_GerminationAssessment, str]:
    """Return the assessment that actually governs publication and presentation.

    A latest-window cluster can be a watchlist item while its stable candidate
    identity has already accumulated enough observations across earlier clusters.
    Publication and detail queries must use the same lifecycle override.
    """
    assessment = cluster.assessment
    lifecycle = candidate.field_evidence.get("candidate_lifecycle") or {}
    lifecycle_windows = tuple(lifecycle.get("observed_window_ids") or ())
    lifecycle_score = float(lifecycle.get("emergence_score") or 0.0)
    lifecycle_qualified = (
        lifecycle.get("status") == "stable_emerging_role"
        and lifecycle_score >= config.emerging_threshold
        and len(lifecycle_windows) >= 3
    )
    if not lifecycle_qualified:
        return assessment, "cluster_assessment"
    return (
        _replace(
            assessment,
            score=max(assessment.score, lifecycle_score),
            qualified=True,
            level="stable_emerging_role",
            decision_reason=(
                "candidate lifecycle reached stable_emerging_role across "
                f"{len(lifecycle_windows)} JD publish-date windows"
            ),
        ),
        "candidate_lifecycle",
    )


def _gate(
    cluster: ClusterRecord, candidate: EmergingCandidate, config: ReleaseGateConfig
) -> ReleaseGateEvidence:
    generated = cluster.generated_definition
    lifecycle = candidate.field_evidence.get("candidate_lifecycle") or {}
    lifecycle_windows = tuple(lifecycle.get("observed_window_ids") or ())
    trajectory = lifecycle_windows or tuple(generated.get("growth_trajectory", ()))
    assessment, _ = _effective_assessment(cluster, candidate, config)
    dimensions = (
        (cluster.assessment.evidence_package.get("emergence_index") or {}).get(
            "dimensions", {}
        )
    )
    formal_experiment = cluster.assessment.evidence_package.get("formal_experiment")
    formal_experiment_accepted = (
        bool(formal_experiment.get("accepted"))
        if formal_experiment
        and formal_experiment.get("experiment_id") == _FORMAL_EXPERIMENT_ID
        else None
    )
    return ReleaseGateEvidence(
        run_succeeded=bool(
            cluster.discovery_run_id and cluster.discovery_run_status == "succeeded"
        ),
        stability_score=cluster.stability_score,
        minimum_stability_score=config.minimum_stability_score,
        assessment=assessment,
        emerging_threshold=config.emerging_threshold,
        evidence_jd_ids=candidate.evidence_jd_ids,
        real_member_count=len(cluster.representative_jd_ids),
        window_count=len(trajectory),
        complete_score_dimensions=set(dimensions)
        == {
            "growth",
            "cross_window_persistence",
            "enterprise_coverage",
            "source_diversity",
            "standard_position_distance",
            "evidence_quality",
            "result_stability",
        },
        complete_definition=_definition_complete(candidate),
        complete_claim_evidence=not _claim_evidence_failures(candidate),
        definition_unchanged_since_approval=candidate.status
        in {EmergingPositionStatus.APPROVED, EmergingPositionStatus.PUBLISHED},
        formal_experiment_accepted=formal_experiment_accepted,
    )


def _definition_payload(candidate: EmergingCandidate) -> dict[str, object]:
    return {
        "position_name": candidate.position_name,
        "core_responsibilities": list(candidate.core_responsibilities),
        "required_skills": _thaw(candidate.required_skills),
        "bonus_skills": _thaw(candidate.bonus_skills),
        "industry_scenarios": list(candidate.industry_scenarios),
        "field_evidence": _thaw(candidate.field_evidence),
    }


def _definition_complete(candidate: EmergingCandidate) -> bool:
    evidence = candidate.field_evidence
    return bool(
        candidate.position_name
        and candidate.core_responsibilities
        and candidate.required_skills
        and candidate.industry_scenarios
        and evidence.get("position_summary", {}).get("content")
        and evidence.get("distinguishing_features", {}).get("content")
        and evidence.get("representative_enterprises", {}).get("content")
        and evidence.get("growth_trajectory", {}).get("content")
    )


def _claim_evidence_failures(candidate: EmergingCandidate) -> tuple[str, ...]:
    fields = candidate.field_evidence
    failures: list[str] = []
    required = {
        "core_responsibilities": list(candidate.core_responsibilities),
        "required_skills": [
            str(item.get("raw_skill") or item.get("normalized_skill_id") or "")
            for item in candidate.required_skills
        ],
        "distinguishing_features": list(
            (fields.get("distinguishing_features") or {}).get("content", ())
        ),
    }
    evidence_keys = {
        "source_jd_id",
        "original_text_snippet",
        "field_type",
        "data_source",
        "window_id",
        "locator",
    }
    for field, claims in required.items():
        field_data = fields.get(field) or {}
        items = field_data.get("items") or ()
        if not claims:
            failures.append(f"{field}: at least one claim is required")
            continue
        for claim in claims:
            item = next((value for value in items if value.get("content") == claim), None)
            evidence = item.get("evidence", ()) if item else ()
            if not evidence or any(
                not evidence_keys <= set(value)
                or not value.get("original_text_snippet")
                or not all((value.get("locator") or {}).values())
                for value in evidence
            ):
                failures.append(f"{field}: claim has no valid locatable Evidence: {claim}")
    return tuple(failures)


def _candidate_from_cluster(
    cluster: ClusterRecord, lifecycle_context: dict[str, object] | None = None
) -> EmergingCandidate:
    generated = cluster.generated_definition
    name = str(generated.get("position_name") or "").strip()
    responsibilities = tuple(generated.get("core_responsibilities") or ())
    required_skills = tuple(generated.get("required_skills") or ())
    if not name or not responsibilities or not required_skills:
        raise DiscoveryEvidenceUnavailable(
            "evidence-backed position name, responsibilities and skills are required"
        )
    field_evidence = _thaw(generated.get("field_evidence") or {})
    lifecycle_context = lifecycle_context or {}
    if lifecycle_context:
        field_evidence["candidate_lifecycle"] = lifecycle_context
    lifecycle_score = float(lifecycle_context.get("emergence_score") or 0.0)
    return EmergingCandidate.create(
        candidate_id=str(_uuid4()),
        cluster_id=cluster.cluster_id,
        position_name=name,
        core_responsibilities=responsibilities,
        required_skills=required_skills,
        bonus_skills=generated.get("bonus_skills") or (),
        industry_scenarios=generated.get("industry_scenarios") or (),
        germination_score=max(cluster.assessment.score, lifecycle_score),
        score_dimensions=cluster.assessment.dimensions,
        evidence_jd_ids=cluster.representative_jd_ids,
        status=EmergingPositionStatus.DRAFT,
        field_evidence=field_evidence,
    )


@_dataclass(frozen=True)
class CreateEmergingCandidate:
    uow_factory: _UoWFactory

    def execute(
        self,
        cluster_id: str,
        actor: EmergingActor,
        lifecycle_context: dict[str, object] | None = None,
    ) -> EmergingRecord:
        _require_admin(actor)
        with self.uow_factory() as uow:
            existing = uow.repository.get_by_cluster(cluster_id)
            if existing is not None:
                return existing
            cluster = uow.repository.get_cluster(cluster_id)
            if cluster is None:
                raise EmergingClusterNotFound("Position cluster not found")
            candidate = _candidate_from_cluster(cluster, lifecycle_context)
            uow.repository.add_candidate(candidate)
            try:
                uow.commit()
            except DuplicateEmergingProjection:
                uow.rollback()
                winner = uow.repository.get_by_cluster(cluster_id)
                if winner is None:
                    raise
                return winner
            record = uow.repository.get(candidate.candidate_id)
            if record is None:
                raise EmergingPositionNotFound(candidate.candidate_id)
            return record


@_dataclass(frozen=True)
class QueryEmergingCandidates:
    uow_factory: _UoWFactory

    def list(self, actor: EmergingActor) -> list[EmergingRecord]:
        with self.uow_factory() as uow:
            return uow.repository.list()

    def get(self, emerging_id: str, actor: EmergingActor) -> EmergingRecord:
        with self.uow_factory() as uow:
            record = uow.repository.get(emerging_id)
            if record is None:
                raise EmergingPositionNotFound("Emerging position not found")
            return record


@_dataclass(frozen=True)
class ImportFormalExperimentResults:
    """Publish the frozen EMERGE v3.2 experiment's 10 emerging roles.

    The formal experiment already passed its 7/7 acceptance gates. Importing
    writes both the position-cluster projection and a published emerging
    position so the results become visible in the governance/publication chain.
    The operation is idempotent: deterministic IDs mean repeated imports do not
    duplicate rows.
    """

    uow_factory: _UoWFactory
    clusters_loader: _Callable[[], tuple[dict[str, object], ...]]

    def execute(self, actor: EmergingActor) -> FormalExperimentImportRecord:
        _require_admin(actor)
        clusters = self.clusters_loader()
        imported = 0
        existing = 0
        imported_keys: list[str] = []
        now = _datetime.now(_timezone.utc).isoformat()
        with self.uow_factory() as uow:
            for item in clusters:
                if str(item.get("state")) != "emerging":
                    continue
                cluster_key = str(item.get("cluster_key") or "").strip()
                canonical_title = str(item.get("canonical_title") or "").strip()
                if not cluster_key or not canonical_title:
                    continue
                counts = dict(item.get("counts") or {})
                growth = dict(item.get("growth") or {})
                observations = int(counts.get("observations") or 0)
                independent_postings = int(counts.get("independent_postings") or 0)
                distinct_dates = int(counts.get("distinct_dates") or 0)
                enterprises = int(counts.get("enterprises") or 0)
                sources = int(counts.get("sources") or 0)
                content_hash_count = int(counts.get("content_hash_count") or 0)
                evidence_refs = tuple(
                    str(ref).strip()
                    for ref in (item.get("evidence_refs") or [])
                    if str(ref).strip()
                )
                cluster_id = str(
                    _uuid5(_UUID_NAMESPACE, f"formal-emerge-v3.2:{cluster_key}")
                )
                assessment = {
                    "state": "emerging",
                    "germination_score": 1.0,
                    "qualified_as_emerging": True,
                    "level": "emerging",
                    "evidence_level": "short_window",
                    "stage1": {
                        "relation": str(
                            item.get("stage1_relation")
                            or "unexplained_structural_novelty"
                        )
                    },
                    "counts": {
                        "observations": observations,
                        "independent_postings": independent_postings,
                        "distinct_dates": distinct_dates,
                        "re_observation_dates": distinct_dates,
                        "enterprises": enterprises,
                        "sources": sources,
                        "content_hash_count": content_hash_count,
                        "market_growth_available": bool(growth.get("available")),
                    },
                    "gates": {
                        "structural_signal": True,
                        "independent_posting_persistence": independent_postings >= 2,
                        "enterprise_diffusion": enterprises >= 2,
                        "source_diffusion": sources >= 2,
                        "diffusion": enterprises >= 2 or sources >= 2,
                        "temporal_persistence_growth_or_evolution": distinct_dates >= 2,
                        "any_temporal_evidence": distinct_dates >= 2,
                    },
                    "reason": (
                        "frozen formal experiment result imported into the "
                        "publication chain; 12-day / 6-date short window only"
                    ),
                    "decision_reason": (
                        "正式实验五项发布门禁均已通过，允许进入人工审核与发布链路"
                    ),
                    "source": "formal_experiment_import",
                    "experiment_id": _FORMAL_EXPERIMENT_ID,
                }
                embedded = item.get("definition")
                if (
                    isinstance(embedded, dict)
                    and embedded.get("position_name")
                    and isinstance(embedded.get("field_evidence"), dict)
                ):
                    definition = {
                        key: value
                        for key, value in embedded.items()
                        if key != "field_evidence"
                    }
                    definition["field_evidence"] = embedded["field_evidence"]
                    position_name = str(definition["position_name"]).strip() or canonical_title
                    core_responsibilities = tuple(
                        str(value)
                        for value in (definition.get("core_responsibilities") or ())
                    )
                    required_skills = tuple(
                        dict(value)
                        for value in (definition.get("required_skills") or ())
                    )
                    bonus_skills = tuple(
                        dict(value)
                        for value in (definition.get("bonus_skills") or ())
                    )
                    industry_scenarios = tuple(
                        str(value)
                        for value in (definition.get("industry_scenarios") or ())
                    )
                    field_evidence = dict(definition["field_evidence"])
                else:
                    definition = {
                        "position_name": canonical_title,
                        "core_responsibilities": [],
                        "required_skills": [],
                        "bonus_skills": [],
                        "industry_scenarios": [],
                        "field_evidence": {
                            "position_name": {"content": [canonical_title]}
                        },
                    }
                    position_name = canonical_title
                    core_responsibilities = ()
                    required_skills = ()
                    bonus_skills = ()
                    industry_scenarios = ()
                    field_evidence = dict(definition["field_evidence"])
                if uow.repository.get_by_cluster(cluster_id) is not None:
                    existing += 1
                    continue
                uow.repository.upsert_formal_experiment_cluster(
                    cluster_id=cluster_id,
                    cluster_name=position_name,
                    sample_count=independent_postings,
                    representative_titles=(position_name,),
                    representative_jd_ids=evidence_refs,
                    discovery_assessment=assessment,
                    generated_definition=definition,
                    discovery_run_id=_FORMAL_DISCOVERY_RUN_ID,
                )
                snapshot = {
                    "published_at": now,
                    "published_by": actor.actor_id,
                    "definition": definition,
                    "emergence_score": 1.0,
                    "germination_assessment": {
                        "qualified_as_emerging": True,
                        "level": "emerging",
                        "decision_reason": (
                            "frozen formal experiment result: "
                            f"{_FORMAL_EXPERIMENT_ID}; 12-day / 6-date short window"
                        ),
                        "qualification_basis": "formal_experiment_import",
                    },
                    "score_dimensions": assessment,
                    "lineage": [],
                    "evidence_jd_ids": list(evidence_refs),
                    "source_experiment_id": _FORMAL_EXPERIMENT_ID,
                }
                candidate = EmergingCandidate.create(
                    candidate_id=cluster_id,
                    cluster_id=cluster_id,
                    position_name=position_name,
                    core_responsibilities=core_responsibilities,
                    required_skills=required_skills,
                    bonus_skills=bonus_skills,
                    industry_scenarios=industry_scenarios,
                    germination_score=1.0,
                    score_dimensions=assessment,
                    evidence_jd_ids=evidence_refs,
                    status=EmergingPositionStatus.PUBLISHED,
                    field_evidence=field_evidence,
                    review_history=(
                        {
                            "reviewer": actor.actor_id,
                            "reviewed_at": now,
                            "conclusion": "approved",
                            "modified": False,
                            "reason": "frozen formal experiment import",
                        },
                    ),
                    published_snapshot=snapshot,
                )
                uow.repository.add_candidate(candidate)
                uow.repository.create_definition_version(candidate, actor.actor_id)
                imported += 1
                imported_keys.append(cluster_key)
            uow.commit()
        return FormalExperimentImportRecord(
            experiment_id=_FORMAL_EXPERIMENT_ID,
            imported=imported,
            existing=existing,
            cluster_keys=tuple(imported_keys),
        )


@_dataclass(frozen=True)
class UpdateEmergingCandidate:
    uow_factory: _UoWFactory

    def execute(
        self, emerging_id: str, changes: EmergingChanges, actor: EmergingActor
    ) -> EmergingRecord:
        _require_admin(actor)
        with self.uow_factory() as uow:
            record = uow.repository.get(emerging_id)
            if record is None:
                raise EmergingPositionNotFound("Emerging position not found")
            candidate = record.candidate.edit_definition(
                position_name=changes.position_name
                if "position_name" in changes.changed_fields
                else None,
                core_responsibilities=changes.core_responsibilities,
                required_skills=changes.required_skills,
                bonus_skills=changes.bonus_skills,
                industry_scenarios=changes.industry_scenarios,
                field_evidence=changes.field_evidence,
            )
            uow.repository.save_candidate(candidate)
            uow.repository.create_definition_version(candidate, actor.actor_id)
            uow.commit()
            return uow.repository.get(emerging_id) or record


@_dataclass(frozen=True)
class SubmitEmergingDefinition:
    uow_factory: _UoWFactory

    def execute(self, emerging_id: str, actor: EmergingActor) -> EmergingRecord:
        _require_admin(actor)
        with self.uow_factory() as uow:
            record = uow.repository.get(emerging_id)
            if record is None:
                raise EmergingPositionNotFound("Emerging position not found")
            if record.candidate.status not in {
                EmergingPositionStatus.DRAFT,
                EmergingPositionStatus.REJECTED,
            }:
                raise InvalidEmergingTransition(
                    "only a draft or rejected definition can be submitted"
                )
            candidate = _replace(
                record.candidate,
                status=EmergingPositionStatus.PENDING_REVIEW,
            )
            uow.repository.save_candidate(candidate)
            uow.commit()
            return uow.repository.get(emerging_id) or record


@_dataclass(frozen=True)
class ReviewEmergingDefinition:
    uow_factory: _UoWFactory

    def execute(
        self,
        emerging_id: str,
        command: ReviewEmergingDefinitionCommand,
        actor: EmergingActor,
    ) -> EmergingRecord:
        _require_admin(actor)
        with self.uow_factory() as uow:
            record = uow.repository.get(emerging_id)
            if record is None:
                raise EmergingPositionNotFound("Emerging position not found")
            candidate = record.candidate
            if candidate.status is not EmergingPositionStatus.PENDING_REVIEW:
                raise InvalidEmergingTransition("only a pending definition can be reviewed")
            if command.conclusion not in {"approved", "rejected"}:
                raise InvalidEmergingTransition(
                    "review conclusion must be approved or rejected"
                )
            modified = any(
                value is not None
                for value in (
                    command.position_name,
                    command.core_responsibilities,
                    command.required_skills,
                    command.field_evidence,
                )
            )
            candidate = candidate.edit_definition(
                position_name=command.position_name,
                core_responsibilities=command.core_responsibilities,
                required_skills=command.required_skills,
                field_evidence=command.field_evidence,
            )
            if command.conclusion == "approved":
                failures = _claim_evidence_failures(candidate)
                if failures or not _definition_complete(candidate):
                    raise ReleaseGateRejected(
                        failures
                        or ("position definition fields are incomplete",)
                    )
            reviewed_at = _datetime.now(_timezone.utc).isoformat()
            review = _freeze(
                {
                    "reviewer": actor.actor_id,
                    "reviewed_at": reviewed_at,
                    "conclusion": command.conclusion,
                    "modified": modified,
                    "reason": command.reason,
                }
            )
            decision = EmergingPositionStatus(command.conclusion)
            candidate = candidate.review(decision)
            candidate = _replace(
                candidate,
                review_history=(*candidate.review_history, review),
            )
            uow.repository.save_candidate(candidate)
            if modified:
                uow.repository.create_definition_version(candidate, actor.actor_id)
            uow.commit()
            return uow.repository.get(emerging_id) or record


@_dataclass(frozen=True)
class DeleteEmergingCandidate:
    uow_factory: _UoWFactory

    def execute(self, emerging_id: str, actor: EmergingActor) -> None:
        _require_admin(actor)
        with self.uow_factory() as uow:
            if uow.repository.get(emerging_id) is None:
                raise EmergingPositionNotFound("Emerging position not found")
            uow.repository.delete_candidate(emerging_id)
            uow.commit()


@_dataclass(frozen=True)
class PublishEmergingCandidate:
    uow_factory: _UoWFactory

    def execute(self, emerging_id: str, actor: EmergingActor) -> EmergingRecord:
        _require_admin(actor)
        with self.uow_factory() as uow:
            record = uow.repository.get(emerging_id)
            if record is None:
                raise EmergingPositionNotFound("Emerging position not found")
            cluster = uow.repository.get_cluster(record.candidate.cluster_id)
            if cluster is None:
                raise EmergingClusterNotFound("Position cluster not found")
            release_config = uow.repository.release_config()
            gate = _gate(cluster, record.candidate, release_config)
            _, qualification_basis = _effective_assessment(
                cluster, record.candidate, release_config
            )
            candidate = record.candidate.publish(gate)
            publication = _freeze(
                {
                    "published_at": _datetime.now(_timezone.utc).isoformat(),
                    "published_by": actor.actor_id,
                    "definition": _definition_payload(candidate),
                    "emergence_score": gate.assessment.score,
                    "germination_assessment": {
                        "qualified_as_emerging": gate.assessment.qualified,
                        "level": gate.assessment.level,
                        "decision_reason": gate.assessment.decision_reason,
                        "qualification_basis": qualification_basis,
                    },
                    "score_dimensions": _thaw(cluster.assessment.evidence_package),
                    "lineage": _thaw(cluster.generated_definition.get("growth_trajectory", ())),
                    "evidence_jd_ids": list(candidate.evidence_jd_ids),
                }
            )
            candidate = _replace(candidate, published_snapshot=publication)
            uow.repository.save_candidate(candidate)
            uow.repository.create_definition_version(candidate, actor.actor_id)
            uow.commit()
            return uow.repository.get(emerging_id) or record


@_dataclass(frozen=True)
class PromoteEmergingCandidate:
    uow_factory: _UoWFactory

    def execute(self, emerging_id: str, actor: EmergingActor) -> StandardPositionRecord:
        _require_admin(actor)
        with self.uow_factory() as uow:
            record = uow.repository.get(emerging_id)
            if record is None:
                raise EmergingPositionNotFound("Emerging position not found")
            cluster = uow.repository.get_cluster(record.candidate.cluster_id)
            if cluster is None:
                raise EmergingClusterNotFound("Position cluster not found")
            record.candidate.assert_promotable(
                _gate(cluster, record.candidate, uow.repository.release_config())
            )
            existing = uow.repository.get_standard_by_source(emerging_id)
            if existing is not None:
                return existing
            standard = uow.repository.add_standard_from(record.candidate)
            try:
                uow.commit()
            except DuplicateEmergingProjection:
                uow.rollback()
                winner = uow.repository.get_standard_by_source(emerging_id)
                if winner is None:
                    raise
                return winner
            return standard


@_dataclass(frozen=True)
class QueryGerminationAssessment:
    uow_factory: _UoWFactory

    def execute(
        self, emerging_id: str, actor: EmergingActor, *, require_admin: bool = False
    ) -> GerminationAssessmentRecord:
        if require_admin:
            _require_admin(actor)
        with self.uow_factory() as uow:
            record = uow.repository.get(emerging_id)
            if record is None:
                raise EmergingPositionNotFound("Emerging position not found")
            cluster = uow.repository.get_cluster(record.candidate.cluster_id)
            if cluster is None or not cluster.discovery_run_id:
                raise DiscoveryEvidenceUnavailable(
                    "Germination assessment is owned by emerging-discovery and is unavailable"
                )
            assessment, qualification_basis = _effective_assessment(
                cluster,
                record.candidate,
                uow.repository.release_config(),
            )
            return GerminationAssessmentRecord(
                emerging_id,
                assessment,
                cluster.discovery_run_id,
                qualification_basis,
            )


@_dataclass(frozen=True)
class GenerateEmergingDefinition:
    uow_factory: _UoWFactory
    formal_clusters_loader: _Callable[[], tuple[dict[str, object], ...]]

    def execute(self, emerging_id: str, actor: EmergingActor) -> GeneratedDefinitionRecord:
        _require_admin(actor)
        with self.uow_factory() as uow:
            record = uow.repository.get(emerging_id)
            if record is None:
                raise EmergingPositionNotFound("Emerging position not found")
            cluster = uow.repository.get_cluster(record.candidate.cluster_id)
            if cluster is None or not cluster.discovery_run_id or not cluster.generated_definition:
                raise DiscoveryEvidenceUnavailable(
                    "Generated definition is owned by emerging-discovery and is unavailable"
                )
            generated = cluster.generated_definition
            if cluster.discovery_run_id == _FORMAL_DISCOVERY_RUN_ID:
                formal_cluster = next(
                    (
                        item
                        for item in self.formal_clusters_loader()
                        if item.get("state") == "emerging"
                        and str(item.get("canonical_title") or "")
                        == cluster.cluster_name
                    ),
                    None,
                )
                if not formal_cluster or not isinstance(formal_cluster.get("definition"), dict):
                    raise DiscoveryEvidenceUnavailable(
                        "Formal evidence-backed position definition is unavailable"
                    )
                generated = formal_cluster["definition"]
            if not (
                generated.get("position_name")
                and generated.get("core_responsibilities")
                and generated.get("required_skills")
                and generated.get("industry_scenarios")
                and (generated.get("field_evidence") or {}).get("position_summary", {}).get("content")
            ):
                raise DiscoveryEvidenceUnavailable(
                    "Evidence-backed position name, summary, responsibilities, skills and industry scenarios are required"
                )
            candidate = record.candidate.edit_definition(
                position_name=generated.get("position_name"),
                core_responsibilities=tuple(
                    generated.get("core_responsibilities", record.candidate.core_responsibilities)
                ),
                required_skills=tuple(
                    generated.get("required_skills", record.candidate.required_skills)
                ),
                bonus_skills=tuple(generated.get("bonus_skills", record.candidate.bonus_skills)),
                industry_scenarios=tuple(
                    generated.get("industry_scenarios", record.candidate.industry_scenarios)
                ),
                field_evidence=generated.get("field_evidence") or {},
            )
            uow.repository.save_candidate(candidate)
            version = uow.repository.create_definition_version(candidate, actor.actor_id)
            uow.commit()
            refreshed = uow.repository.get(emerging_id) or record
            return GeneratedDefinitionRecord(
                refreshed,
                version.version_id,
                "rule_based_evidence_only",
                candidate.evidence_jd_ids,
            )


@_dataclass(frozen=True)
class QueryDefinitionVersions:
    uow_factory: _UoWFactory

    def execute(self, emerging_id: str, actor: EmergingActor) -> list[DefinitionVersionRecord]:
        _require_admin(actor)
        with self.uow_factory() as uow:
            if uow.repository.get(emerging_id) is None:
                raise EmergingPositionNotFound("Emerging position not found")
            return uow.repository.list_definition_versions(emerging_id)


@_dataclass(frozen=True)
class SelectDefinitionVersion:
    uow_factory: _UoWFactory

    def execute(
        self, emerging_id: str, version_id: str, actor: EmergingActor
    ) -> DefinitionSelectionRecord:
        _require_admin(actor)
        with self.uow_factory() as uow:
            selected = uow.repository.select_definition_version(emerging_id, version_id)
            if selected is None:
                raise DefinitionVersionNotFound("Emerging position definition version not found")
            uow.commit()
            candidate, version = selected
            record = uow.repository.get(emerging_id)
            if record is None:
                raise EmergingPositionNotFound("Emerging position not found")
            return DefinitionSelectionRecord(record, version)


@_dataclass(frozen=True)
class EmergingPositionHandlers:
    create: CreateEmergingCandidate
    query: QueryEmergingCandidates
    import_formal: ImportFormalExperimentResults
    update: UpdateEmergingCandidate
    submit_review: SubmitEmergingDefinition
    review: ReviewEmergingDefinition
    delete: DeleteEmergingCandidate
    publish: PublishEmergingCandidate
    promote: PromoteEmergingCandidate
    assessment: QueryGerminationAssessment
    generate_definition: GenerateEmergingDefinition
    versions: QueryDefinitionVersions
    select_version: SelectDefinitionVersion


__all__ = [
    "ClusterRecord",
    "CreateEmergingCandidate",
    "DefinitionSelectionRecord",
    "DefinitionVersionNotFound",
    "DefinitionVersionRecord",
    "DeleteEmergingCandidate",
    "DiscoveryEvidenceUnavailable",
    "DuplicateEmergingProjection",
    "EmergingActor",
    "EmergingCandidate",
    "EmergingChanges",
    "EmergingClusterNotFound",
    "EmergingPositionHandlers",
    "EmergingPositionNotFound",
    "EmergingPositionStatus",
    "EmergingPositionUnitOfWork",
    "EmergingRecord",
    "FormalExperimentImportRecord",
    "GenerateEmergingDefinition",
    "GeneratedDefinitionRecord",
    "GerminationAssessmentRecord",
    "InvalidEmergingTransition",
    "ImportFormalExperimentResults",
    "PermissionDenied",
    "PromoteEmergingCandidate",
    "PublishEmergingCandidate",
    "QueryDefinitionVersions",
    "QueryEmergingCandidates",
    "QueryGerminationAssessment",
    "ReleaseGateConfig",
    "ReleaseGateEvidence",
    "ReleaseGateRejected",
    "ReviewEmergingDefinition",
    "ReviewEmergingDefinitionCommand",
    "SelectDefinitionVersion",
    "StandardPositionRecord",
    "SubmitEmergingDefinition",
    "UpdateEmergingCandidate",
]

del annotations
