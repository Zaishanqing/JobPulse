"""Deterministic request payload fingerprints for the versioned discovery contract."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.application.contracts import AlgorithmSelection, DiscoveryConfig
from app.application.discovery_mapping import (
    normalize_position_reference,
    normalize_snapshot,
    position_reference_contract,
    snapshot_contract,
)
from app.domain.discovery import JDSnapshot, PositionReference
from app.domain.values import thaw


PAYLOAD_FINGERPRINT_VERSION = "discovery-payload-fingerprint.v1"


def _hash(payload: Any) -> str:
    normalized = json.dumps(
        thaw(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _canonical_payload(
    *,
    contract_version: str,
    windows: tuple[Any, ...],
    current_observation_window_id: str,
    algorithm: AlgorithmSelection,
    snapshots: tuple[JDSnapshot, ...],
    config: DiscoveryConfig,
    position_references: tuple[PositionReference, ...],
    input_policy_version: str,
    code_version: str,
    schema_version: str,
) -> dict[str, Any]:
    return {
        "fingerprint_version": PAYLOAD_FINGERPRINT_VERSION,
        "contract_version": contract_version,
        "time_windows": tuple(
            {
                "window_id": item.window_id,
                "start": item.start.isoformat(),
                "end": item.end.isoformat(),
            }
            for item in sorted(
                windows,
                key=lambda value: (value.start, value.end, value.window_id),
            )
        ),
        "current_observation_window_id": current_observation_window_id,
        "algorithm": {
            "canonical_name": algorithm.canonical_name,
            "requested_name": algorithm.requested_name,
            "similarity_threshold": algorithm.similarity_threshold.value,
        },
        "snapshots": tuple(
            snapshot_contract(normalize_snapshot(item))
            for item in sorted(snapshots, key=lambda value: value.jd_id)
        ),
        "config": thaw(config.values),
        "position_references": tuple(
            position_reference_contract(normalize_position_reference(item))
            for item in sorted(
                position_references,
                key=lambda value: value.position_id,
            )
        ),
        "input_policy_version": input_policy_version,
        "code_version": code_version,
        "schema_version": schema_version,
    }


def payload_fingerprint(**kwargs: Any) -> str:
    """Return the canonical payload fingerprint for an execution contract."""
    return _hash(_canonical_payload(**kwargs))


def _persisted_payload_fingerprint(
    config: dict[str, Any],
    snapshot_payloads: list[dict[str, Any]],
) -> str | None:
    """Return a stored fingerprint, or reconstruct one for legacy persisted runs."""
    stored = (config or {}).get("payload_fingerprint") or {}
    if stored.get("hash"):
        return str(stored["hash"])
    run_context = (config or {}).get("run_context") or {}
    time_window = run_context.get("time_window") or {}
    algorithm = run_context.get("algorithm") or {}
    parameters = algorithm.get("parameters") or {}
    windows = time_window.get("windows") or []
    payload = {
        "fingerprint_version": PAYLOAD_FINGERPRINT_VERSION,
        "contract_version": "discovery.v2",
        "time_windows": tuple(
            {
                "window_id": str(item.get("window_id", "")),
                "start": str(item.get("start", "")),
                "end": str(item.get("end", "")),
            }
            for item in sorted(
                windows,
                key=lambda value: (
                    str(value.get("start", "")),
                    str(value.get("end", "")),
                    str(value.get("window_id", "")),
                ),
            )
        ),
        "current_observation_window_id": str(
            time_window.get("current_observation_window_id") or ""
        ),
        "algorithm": {
            "canonical_name": str(algorithm.get("algorithm_name", "")),
            "requested_name": str(algorithm.get("requested_algorithm", "")),
            "similarity_threshold": parameters.get("similarity_threshold", 0.0),
        },
        "snapshots": tuple(
            item
            for item in sorted(
                snapshot_payloads,
                key=lambda value: str(value.get("jd_id", "")),
            )
        ),
        "config": run_context.get("config") or {},
        "position_references": tuple(
            sorted(
                run_context.get("position_references") or [],
                key=lambda value: str(value.get("position_id", "")),
            )
        ),
        "input_policy_version": str(
            ((config or {}).get("input_quality_report") or {}).get(
                "policy_version",
                "",
            )
        ),
        "code_version": str(run_context.get("code_version", "")),
        "schema_version": str(run_context.get("schema_version", "")),
    }
    return _hash(payload)
