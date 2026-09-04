"""Executable replay of the frozen EMERGE v3.2 Stage2 experiment."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from time import perf_counter
import uuid

from app.emergence.emergence_v3_2 import cluster_stage2_with_ablations


REPLAY_BUNDLE_SHA256 = "632d8fba9f86cc75c57a3a20488687cb645475be0879be8bdc5582c17ac4c5f5"
REPLAY_BUNDLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "reports"
    / "emerge-v3.2-formal-replay.json"
)


def _load_bundle() -> tuple[dict, str]:
    payload = REPLAY_BUNDLE_PATH.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != REPLAY_BUNDLE_SHA256:
        raise ValueError("EMERGE v3.2 formal replay bundle digest mismatch")
    bundle = json.loads(payload)
    if (
        bundle.get("schema_version") != "emerge-v3.2-stage2-replay.v1"
        or bundle.get("experiment_id")
        != "EXP-EMERGE-01-CROSSWINDOW-V3.2-20260823"
        or len(bundle.get("clusters") or ()) != 2811
    ):
        raise ValueError("EMERGE v3.2 formal replay bundle contract mismatch")
    return bundle, digest


def _eligible(cluster: dict) -> bool:
    layers = cluster["layers"]
    return bool(cluster["representative"]) and bool(
        layers["re_observation_persistence"]["distinct_dates"] >= 2
        or layers["structural_evolution"]["changed"]
        or layers["market_growth"]["available"]
    )


def _canonical_title(cluster: dict) -> str:
    value = cluster["canonical_title"]
    title = value.get("title") if isinstance(value, dict) else value
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"missing canonical title for cluster {cluster['cluster_key']}")
    return title.strip()


def run_formal_replay() -> dict:
    """Rerun all 2,811 Stage2 decisions and compare with frozen outputs."""
    started_at = datetime.now(timezone.utc)
    started = perf_counter()
    bundle, bundle_digest = _load_bundle()
    expected = bundle["expected"]
    distributions = {
        name: Counter()
        for name in (
            "baseline",
            "no_temporal",
            "no_enterprise_diffusion",
            "no_structural_evolution",
        )
    }
    mismatches: list[dict[str, str]] = []
    emerging: list[dict] = []
    re_observation_only_emerging = 0
    invalid_emerging = 0

    for cluster in bundle["clusters"]:
        decisions = cluster_stage2_with_ablations(
            cluster_relation=cluster["stage1_relation"],
            layers=cluster["layers"],
            structural_evidence=cluster["structural_evidence"],
        )
        for name, decision in decisions.items():
            state = decision["state"]
            if _eligible(cluster):
                distributions[name][state] += 1
            expected_state = cluster["expected_states"][name]
            if state != expected_state:
                mismatches.append(
                    {
                        "cluster_key": cluster["cluster_key"],
                        "mode": name,
                        "expected": expected_state,
                        "actual": state,
                    }
                )
        baseline = decisions["baseline"]
        if baseline["state"] == "emerging":
            counts = baseline["counts"]
            emerging.append(
                {
                    "cluster_key": cluster["cluster_key"],
                    "canonical_title": _canonical_title(cluster),
                    "stage1_relation": cluster["stage1_relation"],
                    "postings": counts["independent_postings"],
                    "enterprises": counts["enterprises"],
                    "sources": counts["sources"],
                }
            )
            if counts["independent_postings"] < 2 or counts["enterprises"] < 2:
                invalid_emerging += 1
        layers = cluster["layers"]
        if (
            layers["re_observation_persistence"]["distinct_dates"] >= 2
            and layers["independent_posting_persistence"]["independent_posting_count"] < 2
            and baseline["state"] == "emerging"
        ):
            re_observation_only_emerging += 1

    baseline_distribution = dict(distributions["baseline"])
    expected_ablations = expected["ablations"]
    ablation_match = all(
        dict(distributions[name]) == expected_ablations[name]["distribution"]
        for name in expected_ablations
    )
    stage1 = expected["stage1_regression"]
    checks = [
        {
            "key": "bundle_integrity",
            "label": "冻结重放资产 SHA-256",
            "mode": "executed",
            "passed": bundle_digest == REPLAY_BUNDLE_SHA256,
            "evidence": bundle_digest,
        },
        {
            "key": "all_stage2_decisions",
            "label": "2,811 个 Stage2 判定逐簇一致",
            "mode": "executed",
            "passed": not mismatches,
            "evidence": f"mismatches={len(mismatches)}",
        },
        {
            "key": "eligible_distribution",
            "label": "2,021 个可判定簇分布一致",
            "mode": "executed",
            "passed": baseline_distribution
            == expected["stage2_distribution_over_eligible"],
            "evidence": json.dumps(baseline_distribution, ensure_ascii=False, sort_keys=True),
        },
        {
            "key": "re_observation_guard",
            "label": "重复观察不会直接形成新兴岗位",
            "mode": "executed",
            "passed": re_observation_only_emerging == 0,
            "evidence": (
                f"{expected['coverage']['re_observation_only']} 个重复观察簇，"
                f"emerging={re_observation_only_emerging}"
            ),
        },
        {
            "key": "independent_evidence_gate",
            "label": "新兴岗位均满足独立发布和企业扩散门",
            "mode": "executed",
            "passed": len(emerging) == 10 and invalid_emerging == 0,
            "evidence": f"emerging={len(emerging)}, invalid={invalid_emerging}",
        },
        {
            "key": "ablations",
            "label": "三组消融分布一致",
            "mode": "executed",
            "passed": ablation_match,
            "evidence": "no_temporal / no_enterprise_diffusion / no_structural_evolution",
        },
        {
            "key": "stage1_regression",
            "label": "Stage1 冻结回归与 v3.1 一致",
            "mode": "frozen_asset_validation",
            "passed": (
                stage1["relation_agreement"] == stage1["samples_compared"] == 127
            ),
            "evidence": (
                f"{stage1['relation_agreement']}/{stage1['samples_compared']}；"
                "本次不重新请求 BGE"
            ),
        },
    ]
    completed_at = datetime.now(timezone.utc)
    return {
        "execution_id": str(uuid.uuid4()),
        "experiment_id": bundle["experiment_id"],
        "algorithm_version": "emerge-v3.2",
        "status": "passed" if all(item["passed"] for item in checks) else "failed",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_ms": round((perf_counter() - started) * 1000, 2),
        "replay_scope": "stage2_full_replay_plus_stage1_frozen_regression_validation",
        "bundle_sha256": bundle_digest,
        "source_results_sha256": bundle["source_results_sha256"],
        "source_diagnostics_sha256": bundle["source_diagnostics_sha256"],
        "cluster_counts": expected["cluster_counts"],
        "stage2_distribution_over_eligible": baseline_distribution,
        "ablations": {
            name: {
                "distribution": dict(distribution),
                "emerging_count": distribution.get("emerging", 0),
            }
            for name, distribution in distributions.items()
            if name != "baseline"
        },
        "emerging_clusters": sorted(emerging, key=lambda item: item["cluster_key"]),
        "checks": checks,
        "passed_checks": sum(item["passed"] for item in checks),
        "total_checks": len(checks),
        "mismatches": mismatches[:20],
        "validation_boundary": (
            "Stage2 本次按生产规则重算；Stage1 仅校验冻结 127/127 回归资产。"
            "标签仍是规则辅助参考，不是独立专家 Gold。"
        ),
    }


__all__ = ["run_formal_replay"]
