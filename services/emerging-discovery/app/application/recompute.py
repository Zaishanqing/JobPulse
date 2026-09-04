"""Isolated Emerging business conclusion recomputation for CF-01."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import TypeAlias

from app.application.contracts import RunDiscoveryCommand
from app.application.discovery import CODE_VERSION, INPUT_SNAPSHOT_SCHEMA_VERSION
from app.application.discovery_identity import discovery_identity
from app.application.discovery_mapping import normalize_position_reference, normalize_snapshot
from app.application.input_quality import INPUT_PRECHECK_POLICY_VERSION, precheck_discovery_input
from app.application.payload_fingerprint import payload_fingerprint
from app.domain.candidate_identity import (
    CandidateIdentitySpec,
    identity_decision,
    select_candidate_identity,
)
from app.domain.discovery import AlgorithmCluster, PositionReference
from app.domain.values import JsonObject, thaw
from app.ports.providers import DiscoveryAlgorithm, ReferencePort


RECOMPUTE_CONTRACT_VERSION = "emerging-conclusion-recompute.v1"
TARGET_SELECTION_VERSION = "emerging-target-selection.v1"

JsonPayload: TypeAlias = (
    dict[str, "JsonPayload"] | list["JsonPayload"] | str | int | float | bool | None
)
UncertaintyInputs: TypeAlias = dict[str, JsonPayload]
ContinuityCertificate: TypeAlias = dict[str, JsonPayload]


@dataclass(frozen=True)
class EmergingTargetAnchor:
    anchor_id: str
    titles: tuple[str, ...]
    skills: tuple[str, ...]
    responsibilities: tuple[str, ...]
    member_jd_ids: tuple[str, ...]
    member_evidence_ids: tuple[str, ...]
    member_template_cluster_ids: tuple[str, ...]
    semantic_centroid: tuple[float, ...] = ()


@dataclass(frozen=True)
class EmergingRecomputeRequest:
    dataset_id: str
    release_id: str
    subject_ref: str
    algorithm_version: str
    config_hash: str
    command: RunDiscoveryCommand
    target_anchor: EmergingTargetAnchor | None = None


@dataclass(frozen=True)
class EmergingRecomputeResult:
    contract_version: str
    dataset_id: str
    release_id: str
    subject_ref: str
    algorithm_version: str
    config_hash: str
    request_fingerprint: str
    score: float
    business_state: str
    rank: int | None
    threshold_passed: bool
    target_found: bool
    candidate_id: str | None
    target_anchor: EmergingTargetAnchor | None
    uncertainty_inputs: UncertaintyInputs
    continuity_certificate: ContinuityCertificate | None
    failure_reason: str | None = None


class RecomputeEmergingConclusion:
    """Run the production discovery algorithm without any persistence adapter."""

    def __init__(self, references: ReferencePort, algorithm: DiscoveryAlgorithm) -> None:
        self.references = references
        self.algorithm = algorithm

    def execute(self, request: EmergingRecomputeRequest) -> EmergingRecomputeResult:
        command = request.command
        resolved = self.references.resolve(command.position_references)
        if not resolved or any(item.graph_version_id == "unavailable" for item in resolved):
            raise ValueError("formal immutable position references are required")
        precheck = precheck_discovery_input(
            tuple(normalize_snapshot(item) for item in command.snapshots),
            time_window_start=command.time_window_start,
            time_window_end=command.time_window_end,
            policy_version=INPUT_PRECHECK_POLICY_VERSION,
        )
        if not precheck.snapshots:
            raise ValueError("no JD snapshots remain after input precheck")
        identity = discovery_identity(
            command,
            resolved,
            execution_snapshots=precheck.snapshots,
            input_policy_version=INPUT_PRECHECK_POLICY_VERSION,
        )
        computed_hash = _config_hash(identity.algorithm.canonical_name, identity.config.values)
        if request.config_hash != computed_hash:
            raise ValueError("emerging recompute config_hash mismatch")
        output = self.algorithm.execute(
            algorithm=identity.algorithm,
            snapshots=identity.snapshots,
            reference_skill_sets=_reference_skill_sets(resolved),
            config=identity.config,
            time_window_ids=[item.window_id for item in command.time_window.windows],
        )
        if request.algorithm_version != output.algorithm_version:
            raise ValueError("emerging recompute algorithm_version mismatch")
        fingerprint = payload_fingerprint(
            contract_version=RECOMPUTE_CONTRACT_VERSION,
            windows=command.time_window.windows,
            current_observation_window_id=command.time_window.current_observation_window.window_id,
            algorithm=identity.algorithm,
            snapshots=identity.snapshots,
            config=identity.config,
            position_references=resolved,
            input_policy_version=INPUT_PRECHECK_POLICY_VERSION,
            code_version=CODE_VERSION,
            schema_version=INPUT_SNAPSHOT_SCHEMA_VERSION,
        )
        ranked = sorted(
            output.clusters,
            key=lambda item: (
                -len(item.members),
                -float(item.assessment.germination_score if item.assessment else 0.0),
                item.key,
            ),
        )
        cluster, rank, continuity, failure = self._target(
            ranked, request.target_anchor, identity.config.values
        )
        if cluster is None:
            return EmergingRecomputeResult(
                contract_version=RECOMPUTE_CONTRACT_VERSION,
                dataset_id=request.dataset_id,
                release_id=request.release_id,
                subject_ref=request.subject_ref,
                algorithm_version=output.algorithm_version,
                config_hash=computed_hash,
                request_fingerprint=fingerprint,
                score=0.0,
                business_state="not_found",
                rank=None,
                threshold_passed=False,
                target_found=False,
                candidate_id=None,
                target_anchor=request.target_anchor,
                uncertainty_inputs={"input_quality_report": thaw(precheck.report)},
                continuity_certificate=continuity,
                failure_reason=failure,
            )
        assessment = cluster.assessment
        if assessment is None:
            raise ValueError("discovery cluster is missing germination assessment")
        anchor = request.target_anchor or _anchor(cluster)
        return EmergingRecomputeResult(
            contract_version=RECOMPUTE_CONTRACT_VERSION,
            dataset_id=request.dataset_id,
            release_id=request.release_id,
            subject_ref=request.subject_ref,
            algorithm_version=output.algorithm_version,
            config_hash=computed_hash,
            request_fingerprint=fingerprint,
            score=assessment.germination_score,
            business_state=assessment.level,
            rank=rank,
            threshold_passed=assessment.qualified_as_emerging,
            target_found=True,
            candidate_id=cluster.key,
            target_anchor=anchor,
            uncertainty_inputs={
                "input_quality_report": thaw(precheck.report),
                "evidence_summary": dict(assessment.evidence_summary),
                "target_selection_version": TARGET_SELECTION_VERSION,
            },
            continuity_certificate=continuity,
        )

    @staticmethod
    def _target(
        ranked: list[AlgorithmCluster],
        anchor: EmergingTargetAnchor | None,
        config,
    ) -> tuple[AlgorithmCluster | None, int | None, dict[str, object] | None, str | None]:
        if not ranked:
            return None, None, None, "no_discovery_clusters"
        if anchor is None:
            return ranked[0], 1, None, None
        match = select_candidate_identity(
            _spec_from_anchor(anchor),
            tuple(_spec(cluster) for cluster in ranked),
            config,
        )
        certificate = asdict(match)
        certificate["decision"] = identity_decision(match)
        if not match.matched or match.abstain or match.candidate_id is None:
            return None, None, certificate, (
                "identity_review_required" if match.abstain else "target_not_found"
            )
        for index, cluster in enumerate(ranked, start=1):
            if cluster.key == match.candidate_id:
                return cluster, index, certificate, None
        return None, None, certificate, "matched_candidate_missing"


def recompute_config_hash(algorithm: str, config: JsonObject) -> str:
    return _config_hash(algorithm, config)


def _config_hash(algorithm: str, config: object) -> str:
    payload = {
        "algorithm": algorithm,
        "config": thaw(config),
        "target_selection_version": TARGET_SELECTION_VERSION,
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _reference_skill_sets(references: tuple[PositionReference, ...]) -> list[set[str]]:
    values = []
    for reference in (normalize_position_reference(item) for item in references):
        values.append({skill.identity.casefold() for skill in reference.required_skills if skill.identity})
    return values


def _anchor(cluster: AlgorithmCluster) -> EmergingTargetAnchor:
    spec = _spec(cluster)
    basis = json.dumps(
        {
            "titles": sorted(spec.titles),
            "skills": sorted(spec.skills),
            "responsibilities": sorted(spec.responsibilities),
            "member_jd_ids": sorted(spec.member_jd_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return EmergingTargetAnchor(
        anchor_id="emerging-anchor:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24],
        titles=tuple(sorted(spec.titles)),
        skills=tuple(sorted(spec.skills)),
        responsibilities=tuple(sorted(spec.responsibilities)),
        member_jd_ids=tuple(sorted(spec.member_jd_ids)),
        member_evidence_ids=tuple(sorted(spec.member_evidence_ids)),
        member_template_cluster_ids=tuple(sorted(spec.member_template_cluster_ids)),
        semantic_centroid=spec.semantic_centroid,
    )


def _spec(cluster: AlgorithmCluster) -> CandidateIdentitySpec:
    evidence_ids: set[str] = set()
    template_ids: set[str] = set()
    for member in cluster.members:
        evidence_ids.update(str(value) for value in member.structured_data.extensions.get("evidence_ids", ()))
        template = member.structured_data.extensions.get("template_cluster_id")
        if template:
            template_ids.add(str(template))
    return CandidateIdentitySpec(
        titles=frozenset(member.title for member in cluster.members),
        skills=frozenset(cluster.core_skills),
        responsibilities=frozenset(cluster.core_responsibilities),
        member_jd_ids=frozenset(member.jd_id for member in cluster.members),
        semantic_centroid=cluster.semantic_centroid,
        candidate_id=cluster.key,
        member_evidence_ids=frozenset(evidence_ids),
        member_template_cluster_ids=frozenset(template_ids),
    )


def _spec_from_anchor(anchor: EmergingTargetAnchor) -> CandidateIdentitySpec:
    return CandidateIdentitySpec(
        titles=frozenset(anchor.titles),
        skills=frozenset(anchor.skills),
        responsibilities=frozenset(anchor.responsibilities),
        member_jd_ids=frozenset(anchor.member_jd_ids),
        semantic_centroid=anchor.semantic_centroid,
        candidate_id=anchor.anchor_id,
        member_evidence_ids=frozenset(anchor.member_evidence_ids),
        member_template_cluster_ids=frozenset(anchor.member_template_cluster_ids),
    )
