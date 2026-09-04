from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence, TypeAlias

from app.contexts.evidence_independence.application import build_summary
from app.contexts.evidence_independence.contracts import (
    ConclusionRecomputePort,
    ConclusionScore,
    EvidenceRecord,
    IndependenceRequest,
    JsonPayload,
)
from app.integrations.emerging_discovery import EmergingDiscoveryClient


EMERGING_CONCLUSION_PROVIDER = "emerging-business-recompute.v1"

RecomputePayload: TypeAlias = dict[str, JsonPayload]
RecomputeResult: TypeAlias = dict[str, JsonPayload]
DiscoveryPayload: TypeAlias = dict[str, JsonPayload]
SnapshotPayload: TypeAlias = dict[str, JsonPayload]
TargetAnchor: TypeAlias = dict[str, JsonPayload]


class EmergingRecomputeTransport(Protocol):
    def recompute_conclusion(self, payload: RecomputePayload) -> RecomputeResult: ...


@dataclass(frozen=True)
class EmergingConclusionConfig:
    dataset_id: str
    release_id: str
    subject_ref: str
    algorithm_version: str
    config_hash: str
    discovery_payload: DiscoveryPayload
    snapshots_by_evidence_id: Mapping[str, SnapshotPayload]
    target_anchor: TargetAnchor | None = None

    def __post_init__(self) -> None:
        for field in (
            "dataset_id",
            "release_id",
            "subject_ref",
            "algorithm_version",
            "config_hash",
        ):
            if not str(getattr(self, field)).strip():
                raise ValueError(f"{field} is required")
        if not self.config_hash.startswith("sha256:"):
            raise ValueError("config_hash must be a sha256 identity")


class EmergingConclusionRecomputeAdapter(ConclusionRecomputePort):
    provider = EMERGING_CONCLUSION_PROVIDER

    def __init__(
        self,
        config: EmergingConclusionConfig,
        transport: EmergingRecomputeTransport,
    ) -> None:
        self.config = config
        self.transport = transport
        self._target_anchor = dict(config.target_anchor) if config.target_anchor else None
        self._fingerprints: dict[str, str] = {}

    def evaluate(
        self,
        records: Sequence[EvidenceRecord],
        request: IndependenceRequest,
    ) -> ConclusionScore:
        summary = build_summary(records, request)
        missing = sorted(
            record.evidence_id
            for record in records
            if record.evidence_id not in self.config.snapshots_by_evidence_id
        )
        if missing:
            return ConclusionScore(
                score=0.0,
                state="blocked",
                failure_reasons=("emerging_snapshot_missing", *missing),
                business_state="not_run",
                target_found=False,
            )
        payload = dict(self.config.discovery_payload)
        evidence_ids = tuple(sorted(record.evidence_id for record in records))
        subset_identity = hashlib.sha256(
            json.dumps(evidence_ids, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        payload.update(
            {
                "request_id": f"cf01:{self.config.dataset_id}:{subset_identity[:24]}",
                "dataset_id": self.config.dataset_id,
                "release_id": self.config.release_id,
                "subject_ref": self.config.subject_ref,
                "algorithm_version": self.config.algorithm_version,
                "config_hash": self.config.config_hash,
                "snapshots": [
                    dict(self.config.snapshots_by_evidence_id[evidence_id])
                    for evidence_id in evidence_ids
                ],
                "target_anchor": self._target_anchor,
            }
        )
        result = self.transport.recompute_conclusion(payload)
        self._validate_binding(result)
        response_fingerprint = str(result["request_fingerprint"])
        previous = self._fingerprints.setdefault(subset_identity, response_fingerprint)
        if previous != response_fingerprint:
            raise ValueError("emerging recompute payload conflict")
        if self._target_anchor is None and isinstance(result.get("target_anchor"), Mapping):
            self._target_anchor = dict(result["target_anchor"])
        failure_reasons = list(summary.uncertainty_reasons)
        if result.get("failure_reason"):
            failure_reasons.append(str(result["failure_reason"]))
        return ConclusionScore(
            score=float(result["score"]),
            state=summary.uncertainty_state,
            rank=(int(result["rank"]) if result.get("rank") is not None else None),
            threshold_crossed=bool(result["threshold_passed"]),
            failure_reasons=tuple(dict.fromkeys(failure_reasons)),
            business_state=str(result["business_state"]),
            target_found=bool(result["target_found"]),
            candidate_identity=(
                str(result["candidate_id"]) if result.get("candidate_id") else None
            ),
            continuity_certificate=(
                dict(result["continuity_certificate"])
                if isinstance(result.get("continuity_certificate"), Mapping)
                else None
            ),
        )

    def _validate_binding(self, result: Mapping[str, object]) -> None:
        expected = {
            "contract_version": "emerging-conclusion-recompute.v1",
            "dataset_id": self.config.dataset_id,
            "release_id": self.config.release_id,
            "subject_ref": self.config.subject_ref,
            "algorithm_version": self.config.algorithm_version,
            "config_hash": self.config.config_hash,
        }
        mismatched = [key for key, value in expected.items() if result.get(key) != value]
        if mismatched:
            raise ValueError(
                "emerging recompute identity mismatch: " + ", ".join(mismatched)
            )


def build_emerging_conclusion_provider(
    config: EmergingConclusionConfig,
    transport: EmergingRecomputeTransport,
) -> EmergingConclusionRecomputeAdapter:
    return EmergingConclusionRecomputeAdapter(config, transport)


def load_emerging_conclusion_provider(
    emerging_id: str,
    release_id: str | None,
    manifest_path: str | None = None,
    transport: EmergingRecomputeTransport | None = None,
) -> ConclusionRecomputePort | None:
    """Compose the frozen recompute provider from a release manifest.

    Manifest parsing and concrete HTTP transport construction stay in the
    application layer so API dependencies only depend on the port.
    """
    if release_id is None or manifest_path is None:
        return None
    path = Path(manifest_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("emerging conclusion manifest must be an object")
    expected = {
        "contract_version": "emerging-conclusion-provider-manifest.v1",
        "release_id": release_id,
        "subject_ref": emerging_id,
    }
    mismatched = [
        key for key, value in expected.items() if payload.get(key) != value
    ]
    if mismatched:
        raise ValueError(
            "emerging conclusion manifest identity mismatch: "
            + ", ".join(mismatched)
        )
    snapshots = payload.get("snapshots_by_evidence_id")
    discovery_payload = payload.get("discovery_payload")
    if not isinstance(snapshots, Mapping) or not isinstance(
        discovery_payload, Mapping
    ):
        raise ValueError("emerging conclusion manifest payload is incomplete")
    config = EmergingConclusionConfig(
        dataset_id=str(payload["dataset_id"]),
        release_id=release_id,
        subject_ref=emerging_id,
        algorithm_version=str(payload["algorithm_version"]),
        config_hash=str(payload["config_hash"]),
        discovery_payload=discovery_payload,
        snapshots_by_evidence_id=snapshots,
        target_anchor=(
            payload.get("target_anchor")
            if isinstance(payload.get("target_anchor"), Mapping)
            else None
        ),
    )
    return build_emerging_conclusion_provider(
        config, transport or EmergingDiscoveryClient()
    )


__all__ = [
    "EMERGING_CONCLUSION_PROVIDER",
    "EmergingConclusionConfig",
    "EmergingConclusionRecomputeAdapter",
    "EmergingRecomputeTransport",
    "build_emerging_conclusion_provider",
    "load_emerging_conclusion_provider",
]
