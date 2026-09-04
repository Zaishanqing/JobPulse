from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest

from app.contexts.evidence_independence.contracts import (
    EvidenceRecord,
    IndependenceRequest,
)
from app.contexts.evidence_independence.emerging import (
    EmergingConclusionConfig,
    EmergingConclusionRecomputeAdapter,
)


class _Transport:
    def __init__(self, *, wrong_release: bool = False) -> None:
        self.wrong_release = wrong_release
        self.payloads: list[dict[str, object]] = []

    def recompute_conclusion(self, payload: dict[str, object]) -> dict[str, object]:
        self.payloads.append(payload)
        snapshot_count = len(payload["snapshots"])
        fingerprint = "sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return {
            "contract_version": "emerging-conclusion-recompute.v1",
            "dataset_id": payload["dataset_id"],
            "release_id": "wrong" if self.wrong_release else payload["release_id"],
            "subject_ref": payload["subject_ref"],
            "algorithm_version": payload["algorithm_version"],
            "config_hash": payload["config_hash"],
            "request_fingerprint": fingerprint,
            "score": snapshot_count / 10,
            "business_state": "stable" if snapshot_count >= 3 else "weak",
            "rank": 1 if snapshot_count else None,
            "threshold_passed": snapshot_count >= 3,
            "target_found": bool(snapshot_count),
            "candidate_id": "candidate-1" if snapshot_count else None,
            "target_anchor": {"candidate_id": "candidate-1"},
            "continuity_certificate": {"status": "continuous"},
            "failure_reason": None,
        }


def _adapter(transport: _Transport) -> EmergingConclusionRecomputeAdapter:
    return EmergingConclusionRecomputeAdapter(
        EmergingConclusionConfig(
            dataset_id="dataset-1",
            release_id="release-1",
            subject_ref="BACKEND_ENGINEER",
            algorithm_version="agglomerative-tfidf-v1:baseline",
            config_hash="sha256:" + "a" * 64,
            discovery_payload={"algorithm": "baseline", "config": {}},
            snapshots_by_evidence_id={
                f"e-{index}": {"jd_id": f"jd-{index}"}
                for index in range(1, 5)
            },
        ),
        transport,
    )


def _records() -> list[EvidenceRecord]:
    return [
        EvidenceRecord(
            evidence_id=f"e-{index}",
            subject_ref="BACKEND_ENGINEER",
            source_id=f"source-{index}",
            enterprise_id=f"enterprise-{index}",
            template_cluster_id=f"template-{index}",
            published_at=date(2026, 1, index),
            release_id="release-1",
        )
        for index in range(1, 5)
    ]


def test_adapter_uses_exact_subset_and_freezes_target_anchor() -> None:
    transport = _Transport()
    adapter = _adapter(transport)
    request = IndependenceRequest(
        subject_ref="BACKEND_ENGINEER",
        release_id="release-1",
        observation_reference_date=date(2026, 1, 31),
    )

    baseline = adapter.evaluate(_records(), request)
    ablated = adapter.evaluate(_records()[1:], request)

    assert baseline.business_state == "stable"
    assert baseline.target_found is True
    assert baseline.candidate_identity == "candidate-1"
    assert len(transport.payloads[0]["snapshots"]) == 4
    assert transport.payloads[0]["target_anchor"] is None
    assert transport.payloads[1]["target_anchor"] == {"candidate_id": "candidate-1"}
    assert ablated.threshold_crossed is True


def test_adapter_fails_closed_on_response_identity_drift() -> None:
    adapter = _adapter(_Transport(wrong_release=True))
    request = IndependenceRequest(
        subject_ref="BACKEND_ENGINEER",
        release_id="release-1",
    )
    with pytest.raises(ValueError, match="release_id"):
        adapter.evaluate(_records(), request)


def test_adapter_blocks_when_frozen_snapshot_is_missing() -> None:
    adapter = _adapter(_Transport())
    records = _records() + [
        EvidenceRecord(
            evidence_id="missing",
            subject_ref="BACKEND_ENGINEER",
            source_id="source-x",
        )
    ]
    result = adapter.evaluate(
        records,
        IndependenceRequest(subject_ref="BACKEND_ENGINEER", release_id="release-1"),
    )
    assert result.state == "blocked"
    assert result.failure_reasons == ("emerging_snapshot_missing", "missing")
