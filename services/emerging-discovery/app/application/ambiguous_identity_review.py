"""Read-only D28 export of D16 ambiguous identity evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.domain.candidate_identity import (
    PENDING_IDENTITY_REVIEW,
    RESOLVED_NEW,
    RESOLVED_SAME,
)
from app.domain.values import FrozenDict, JsonObject, freeze, thaw
from app.ports.providers import DiscoveryUnitOfWork
from app.ports.records import (
    AmbiguousIdentityPairRecord,
    CandidateRecord,
    IdentityResolutionAuditRecord,
)


PACKAGE_SCHEMA_VERSION = "ambiguous-identity-evidence-package.v1"
EXPORT_SCHEMA_VERSION = "ambiguous-identity-evidence-export.v1"
B_REVIEW_CONTRACT_VERSION = "review-task.v1"


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _values_status(left: Sequence[str], right: Sequence[str]) -> dict[str, Any]:
    left_values = sorted(set(left))
    right_values = sorted(set(right))
    if not left_values or not right_values:
        status = "missing"
    elif set(left_values) & set(right_values):
        status = "match"
    else:
        status = "mismatch"
    return {"status": status, "candidate_a": left_values, "candidate_b": right_values}


def _component_status(value: object, *, semantic_status: str, semantic: bool = False) -> str:
    if semantic and semantic_status == "unavailable":
        return "unavailable"
    if value is None:
        return "missing"
    return "match" if float(value) == 1.0 else "mismatch"


def _profile_values(candidate: CandidateRecord, name: str) -> tuple[str, ...]:
    return tuple(str(item) for item in getattr(candidate, name))


def _snapshot_values(pair: AmbiguousIdentityPairRecord, side: str, name: str) -> tuple[str, ...]:
    snapshots = getattr(pair, f"candidate_{side}_snapshots")
    return tuple(
        sorted(
            {
                str(value)
                for item in snapshots
                if (value := getattr(item, name)) is not None and str(value).strip()
            }
        )
    )


def build_ambiguous_identity_package(pair: AmbiguousIdentityPairRecord) -> JsonObject:
    observation = pair.observation
    match = dict(observation.match_evidence)
    certificate = dict(match.get("continuity_certificate") or {})
    components = dict(certificate.get("components") or match.get("components") or {})
    semantic_status = str(
        certificate.get("semantic_status") or match.get("semantic_status") or "unavailable"
    )
    component_evidence = {
        name: {
            "value": components.get(name),
            "status": _component_status(
                components.get(name),
                semantic_status=semantic_status,
                semantic=name == "semantic_similarity",
            ),
        }
        for name in (
            "title_similarity",
            "skill_similarity",
            "responsibility_similarity",
            "membership_overlap",
            "semantic_similarity",
            "sample_overlap",
            "dedup_cluster_overlap",
            "template_cluster_overlap",
        )
    }
    comparisons = {
        "title_overlap": component_evidence["title_similarity"],
        "skill_overlap": component_evidence["skill_similarity"],
        "responsibility_overlap": component_evidence["responsibility_similarity"],
        "company": _values_status(
            _snapshot_values(pair, "a", "company"),
            _snapshot_values(pair, "b", "company"),
        ),
        "source": _values_status(
            _snapshot_values(pair, "a", "source_name"),
            _snapshot_values(pair, "b", "source_name"),
        ),
        "template_identity": _values_status(
            _profile_values(pair.candidate_a, "member_template_cluster_ids"),
            _profile_values(pair.candidate_b, "member_template_cluster_ids"),
        ),
        "dedup_identity": _values_status(
            _profile_values(pair.candidate_a, "member_dedup_cluster_ids"),
            _profile_values(pair.candidate_b, "member_dedup_cluster_ids"),
        ),
        "semantic": {
            **component_evidence["semantic_similarity"],
            "semantic_status": semantic_status,
        },
    }
    evidence_refs = sorted(
        {
            *(str(item) for item in certificate.get("evidence_refs") or ()),
            *(item.evidence_ref for item in pair.candidate_a_snapshots),
            *(item.evidence_ref for item in pair.candidate_b_snapshots),
        }
    )
    content = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "readonly": True,
        "traceability": {
            "observation_id": observation.id,
            "run_id": observation.run_id,
            "window_id": observation.window_id,
            "cluster_id": observation.cluster_id,
        },
        "observation_id": observation.id,
        "candidate_a": pair.candidate_a.id,
        "candidate_b": pair.candidate_b.id,
        "decision": "review_required",
        "decision_margin": certificate.get("margin", match.get("margin")),
        "threshold": certificate.get("threshold", match.get("threshold")),
        "decision_reason": certificate.get(
            "decision_reason", match.get("decision_reason")
        ),
        "abstention_reason": certificate.get(
            "abstention_reason", match.get("abstention_reason")
        ),
        "similarity_components": component_evidence,
        "comparisons": comparisons,
        "algorithm_version": certificate.get(
            "algorithm_version", match.get("decision_version")
        ),
        "config_version": certificate.get("config_version", match.get("config_version")),
        "abstention_policy_version": certificate.get("abstention_policy_version"),
        "evidence_refs": evidence_refs,
    }
    package_id = "d28:" + _canonical_hash(content).removeprefix("sha256:")[:32]
    review_interface = {
        "contract_version": B_REVIEW_CONTRACT_VERSION,
        "interface_mode": "readonly_evidence_package",
        "source_system": "emerging-discovery",
        "task_kind": "ambiguous_identity_pair",
        "object_type": "ambiguous_identity_pair",
        "object_id": package_id,
        "reason": content["abstention_reason"] or content["decision_reason"],
        "risk_level": "medium",
        "evidence_count": len(evidence_refs),
        "evidence_context": {
            "evidence": evidence_refs,
            "original_values": {"candidate_b": pair.candidate_b.id},
            "current_values": {"candidate_a": pair.candidate_a.id},
            "modified_values": {},
            "impacted_relations": [],
            "review_flags": ["ambiguous_identity_pair"],
            "impact_scope": {
                "run_id": observation.run_id,
                "window_id": observation.window_id,
            },
            "history": [],
        },
    }
    package = {
        **content,
        "package_id": package_id,
        "package_identity": _canonical_hash(content),
        "review_interface": review_interface,
    }
    frozen = freeze(package)
    assert isinstance(frozen, FrozenDict)
    return frozen


@dataclass(frozen=True)
class ExportAmbiguousIdentityEvidence:
    uow: DiscoveryUnitOfWork

    def execute(self, *, observation_id: str | None = None) -> JsonObject:
        with self.uow:
            sources = self.uow.candidates.ambiguous_identity_pairs(observation_id)
        packages = tuple(build_ambiguous_identity_package(item) for item in sources)
        if observation_id is not None and not packages:
            raise LookupError("Ambiguous identity observation not found")
        value = freeze(
            {
                "schema_version": EXPORT_SCHEMA_VERSION,
                "readonly": True,
                "package_count": len(packages),
                "packages": packages,
                "filters": {"observation_id": observation_id},
            }
        )
        assert isinstance(value, FrozenDict)
        return value


@dataclass(frozen=True)
class ResolveAmbiguousIdentityCommand:
    provisional_candidate_id: str
    resolution: str
    reviewer: str
    reason: str
    target_candidate_id: str | None = None
    expected_version: str | None = None
    idempotency_key: str | None = None


def _merge_provisional_into_canonical(
    canonical: CandidateRecord,
    provisional: CandidateRecord,
    resolved_at: datetime,
) -> CandidateRecord:
    observed_windows = tuple(
        dict.fromkeys(
            (*canonical.observed_window_ids, *provisional.observed_window_ids)
        )
    )
    current_cluster_id = (
        provisional.current_cluster_id or canonical.current_cluster_id
    )
    previous_cluster_ids = tuple(
        dict.fromkeys(
            item
            for item in (
                canonical.current_cluster_id,
                *canonical.previous_cluster_ids,
                *provisional.previous_cluster_ids,
            )
            if item and item != current_cluster_id
        )
    )
    latest = (
        provisional
        if provisional.current_cluster_id == current_cluster_id
        else canonical
    )
    return replace(
        canonical,
        last_seen_window_id=(
            provisional.last_seen_window_id
            if provisional.last_seen_window_id
            not in canonical.observed_window_ids
            else canonical.last_seen_window_id
        ),
        age=max(canonical.age, len(observed_windows)),
        current_cluster_id=current_cluster_id,
        previous_cluster_ids=previous_cluster_ids,
        display_title=latest.display_title,
        support_count=latest.support_count,
        company_coverage=latest.company_coverage,
        identity_similarity=latest.identity_similarity,
        novelty_score=latest.novelty_score,
        emergence_score=latest.emergence_score,
        evidence=latest.evidence,
        identity_stability=latest.identity_stability,
        titles=sorted(set(canonical.titles) | set(provisional.titles)),
        skills=sorted(set(canonical.skills) | set(provisional.skills)),
        responsibilities=sorted(
            set(canonical.responsibilities) | set(provisional.responsibilities)
        ),
        member_jd_ids=sorted(
            set(canonical.member_jd_ids) | set(provisional.member_jd_ids)
        ),
        observed_window_ids=observed_windows,
        semantic_centroid=(
            provisional.semantic_centroid
            if provisional.semantic_centroid
            else canonical.semantic_centroid
        ),
        evidence_titles=sorted(
            set(canonical.evidence_titles) | set(provisional.evidence_titles)
        ),
        evidence_skills=sorted(
            set(canonical.evidence_skills) | set(provisional.evidence_skills)
        ),
        evidence_responsibilities=sorted(
            set(canonical.evidence_responsibilities)
            | set(provisional.evidence_responsibilities)
        ),
        member_evidence_ids=sorted(
            set(canonical.member_evidence_ids)
            | set(provisional.member_evidence_ids)
        ),
        member_dedup_cluster_ids=sorted(
            set(canonical.member_dedup_cluster_ids)
            | set(provisional.member_dedup_cluster_ids)
        ),
        member_template_cluster_ids=sorted(
            set(canonical.member_template_cluster_ids)
            | set(provisional.member_template_cluster_ids)
        ),
        updated_at=resolved_at,
    )


@dataclass(frozen=True)
class ResolveAmbiguousIdentity:
    uow: DiscoveryUnitOfWork

    def execute(self, command: ResolveAmbiguousIdentityCommand) -> IdentityResolutionAuditRecord:
        if command.resolution not in {"confirm_same", "confirm_new"}:
            raise ValueError(
                "resolution must be confirm_same or confirm_new"
            )
        with self.uow:
            candidate = self.uow.candidates.candidate(command.provisional_candidate_id)
            if candidate is None:
                raise LookupError("Candidate not found")

            if command.idempotency_key:
                key_audits = self.uow.candidates.identity_resolution_audits(
                    idempotency_key=command.idempotency_key
                )
                if key_audits:
                    existing = key_audits[0]
                    if (
                        existing.provisional_candidate_id == candidate.id
                        and existing.decision == command.resolution
                        and existing.target_candidate_id
                        == command.target_candidate_id
                    ):
                        return existing
                    raise ValueError(
                        "idempotency key already used for a different identity resolution"
                    )

            existing_audits = self.uow.candidates.identity_resolution_audits(
                provisional_candidate_id=candidate.id
            )
            if candidate.identity_resolution_state in (RESOLVED_SAME, RESOLVED_NEW):
                if existing_audits:
                    latest = existing_audits[-1]
                    if (
                        latest.decision == command.resolution
                        and latest.target_candidate_id == command.target_candidate_id
                    ):
                        return latest
                raise ValueError(
                    "candidate identity already resolved; conflicting resolution rejected"
                )
            if (
                candidate.identity_resolution_state != PENDING_IDENTITY_REVIEW
                and candidate.identity_certificate.get("decision")
                != "review_required"
            ):
                raise ValueError("candidate identity is not pending review")

            pending = dict(thaw(candidate.identity_resolution or {}))
            certificate = dict(thaw(candidate.identity_certificate or {}))
            closest_id = pending.get("closest_candidate_id") or certificate.get(
                "closest_candidate_id"
            )
            actual_version = pending.get("algorithm_version") or certificate.get(
                "algorithm_version"
            )
            if (
                command.expected_version is not None
                and actual_version is not None
                and command.expected_version != actual_version
            ):
                raise ValueError(
                    "expected identity algorithm version does not match persisted decision"
                )

            target_candidate_id: str | None = None
            target_record: CandidateRecord | None = None
            if command.resolution == "confirm_same":
                if not command.target_candidate_id:
                    raise ValueError("confirm_same requires target_candidate_id")
                if closest_id and command.target_candidate_id != closest_id:
                    raise ValueError(
                        "confirm_same target must match the persisted closest candidate"
                    )
                target_record = self.uow.candidates.candidate(
                    command.target_candidate_id
                )
                if target_record is None:
                    raise LookupError("Target candidate not found")
                if (
                    target_record.identity_resolution_state
                    == PENDING_IDENTITY_REVIEW
                ):
                    raise ValueError("target candidate is itself pending review")
                target_candidate_id = target_record.id
            else:
                if command.target_candidate_id is not None:
                    raise ValueError("confirm_new must not provide target_candidate_id")

            now = datetime.now(timezone.utc)
            idempotency_key = command.idempotency_key or (
                f"resolve:{candidate.id}:{command.resolution}:"
                f"{target_candidate_id or 'new'}"
            )
            window_id = pending.get("window_id") or certificate.get("window_id")
            audit = IdentityResolutionAuditRecord(
                resolution_id=str(uuid4()),
                provisional_candidate_id=candidate.id,
                target_candidate_id=target_candidate_id,
                decision=command.resolution,
                reviewer=command.reviewer,
                reason=command.reason,
                window_id=str(window_id) if window_id else None,
                timestamp=now,
                algorithm_version=(
                    str(actual_version) if actual_version is not None else None
                ),
                idempotency_key=idempotency_key,
                created_at=now,
                details=freeze(
                    {
                        "expected_version": command.expected_version,
                        "persisted_algorithm_version": actual_version,
                        "cluster_id": pending.get("cluster_id"),
                    }
                ),
            )

            resolution_state = (
                RESOLVED_SAME
                if command.resolution == "confirm_same"
                else RESOLVED_NEW
            )
            resolved_payload = freeze(
                {
                    **pending,
                    "state": resolution_state,
                    "resolution": {
                        "resolution_id": audit.resolution_id,
                        "decision": audit.decision,
                        "target_candidate_id": audit.target_candidate_id,
                        "reviewer": audit.reviewer,
                        "reason": audit.reason,
                        "window_id": audit.window_id,
                        "timestamp": audit.timestamp.isoformat(),
                        "algorithm_version": audit.algorithm_version,
                        "idempotency_key": audit.idempotency_key,
                    },
                }
            )
            resolved = replace(
                candidate,
                identity_resolution_state=resolution_state,
                identity_resolution=resolved_payload,
                canonical_candidate_id=(
                    target_candidate_id
                    if command.resolution == "confirm_same"
                    else None
                ),
                updated_at=now,
            )
            self.uow.candidates.save(resolved)
            if command.resolution == "confirm_same" and target_record is not None:
                self.uow.candidates.save(
                    _merge_provisional_into_canonical(
                        target_record,
                        resolved,
                        now,
                    )
                )
            self.uow.candidates.add_identity_resolution_audit(audit)
            self.uow.commit()
            return audit
