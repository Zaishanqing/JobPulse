"""Build the compact, immutable EMERGE v3.2 Stage2 replay bundle.

This script only projects fields already frozen by EXP-EMERGE-01. It does not
change labels, thresholds, weights, or decisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


EXPERIMENT_ID = "EXP-EMERGE-01-CROSSWINDOW-V3.2-20260823"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _structural_evidence(record: dict) -> dict:
    details = record["stage2"]["gates"]["structural_signal_details"]
    return {
        key: details[key]
        for key in (
            "reference_family",
            "reference_core_skills_non_empty",
            "reference_core_domains",
            "candidate_skill_domains",
            "explanation_combined",
            "reference_core_inherited",
        )
        if key in details
    }


def _project(record: dict) -> dict:
    return {
        "cluster_key": record["cluster_key"],
        "canonical_title": record.get("representative") or record["cluster_key"],
        "representative": bool(record.get("representative")),
        "stage1_relation": record["stage1_relation"],
        "layers": record["temporal_layers"],
        "structural_evidence": _structural_evidence(record),
        "expected_states": {
            "baseline": record["stage2"]["state"],
            **record["ablations"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    results = json.loads(args.results.read_text(encoding="utf-8"))
    clusters = [
        _project(json.loads(line))
        for line in args.diagnostics.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if results.get("experiment_id") != EXPERIMENT_ID or len(clusters) != 2811:
        raise ValueError("Unexpected formal experiment assets")
    payload = {
        "schema_version": "emerge-v3.2-stage2-replay.v1",
        "experiment_id": EXPERIMENT_ID,
        "source_results_sha256": _sha256(args.results),
        "source_diagnostics_sha256": _sha256(args.diagnostics),
        "expected": {
            "cluster_counts": results["cluster_counts"],
            "stage2_distribution_over_eligible": results[
                "stage2_distribution_over_eligible"
            ],
            "coverage": results["coverage"],
            "ablations": results["ablations"],
            "stage1_regression": results["stage1_regression_vs_v31"],
        },
        "clusters": clusters,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {len(clusters)} clusters to {args.output}")
    print(f"sha256={_sha256(args.output)}")


if __name__ == "__main__":
    main()
