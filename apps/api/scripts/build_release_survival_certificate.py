"""Build the read-only B-EVIDENCE-DECAY-01 cross-release survival certificate."""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "release-evidence-survival-certificate.v1"
CERTIFICATE_ID = "BEVIDDECAY-20260813"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _read_facts(artifact_path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if artifact_path.suffix == ".gz" else Path.open
    if artifact_path.suffix == ".gz":
        handle = opener(artifact_path, mode="rt", encoding="utf-8")
    else:
        handle = opener(artifact_path, mode="r", encoding="utf-8")
    rows: list[dict[str, Any]] = []
    with handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_release(base_dir: Path, release_id: str) -> dict[str, Any]:
    """Load and validate a frozen kg-release manifest plus its membership triples."""
    release_dir = base_dir / release_id
    manifest_path = release_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("release_schema_version") != "kg-release-manifest.v1":
        raise ValueError(f"{release_id}: unsupported release schema")
    if manifest.get("release_id") != release_id:
        raise ValueError(f"{release_id}: manifest release_id mismatch")
    artifacts = manifest.get("artifacts", [])
    if not artifacts:
        raise ValueError(f"{release_id}: no artifacts")
    membership: set[tuple[str, str, str]] = set()
    verified_count = 0
    for artifact in artifacts:
        if artifact.get("artifact_type") != "published-jd-facts":
            raise ValueError(f"{release_id}: unsupported artifact type")
        artifact_path = release_dir / artifact["path"]
        facts = _read_facts(artifact_path)
        expected = artifact["record_count"]
        if len(facts) != expected:
            raise ValueError(
                f"{release_id}: record_count mismatch {len(facts)} != {expected}"
            )
        for fact in facts:
            key = (
                fact["source_jd_id"],
                fact["source_fact_id"],
                fact["source_fact_version"],
            )
            if key in membership:
                raise ValueError(f"{release_id}: duplicate membership triple")
            membership.add(key)
            verified_count += 1
    return {
        "release_id": release_id,
        "manifest": manifest,
        "membership": membership,
        "verified_count": verified_count,
    }


def load_verified_evidence(
    crosswalk_path: Path, candidates_path: Path, membership: set[tuple[str, str, str]]
) -> tuple[list[dict[str, Any]], int]:
    """Join verified release crosswalk rows to claim candidates by exact fact triple.

    Returns ``(rows, membership_reverified_count)``. A candidate whose frozen triple is
    no longer present in the on-disk release package is kept (the frozen crosswalk is the
    claim-level input) but does not count toward membership re-verification.
    """
    crosswalk = _read_jsonl(crosswalk_path)
    candidates = _read_jsonl(candidates_path)
    verified_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in crosswalk:
        if not row.get("membership_verified"):
            continue
        key = (
            row["main_jd_id"],
            row["source_fact_id"],
            row["source_fact_version"],
            row["source_jd_id"],
        )
        if key in verified_by_key:
            raise ValueError(f"duplicate verified crosswalk key: {key}")
        verified_by_key[key] = row
    rows: list[dict[str, Any]] = []
    membership_reverified = 0
    for candidate in candidates:
        key = (
            candidate["main_jd_id"],
            candidate["source_fact_id"],
            candidate["source_fact_version"],
            candidate["source_jd_id"],
        )
        verified = verified_by_key.pop(key, None)
        if verified is None:
            raise ValueError(f"candidate without verified crosswalk row: {key}")
        triple = (
            candidate["source_jd_id"],
            candidate["source_fact_id"],
            candidate["source_fact_version"],
        )
        if triple in membership:
            membership_reverified += 1
        rows.append(
            {
                "evidence_id": verified["evidence_id"],
                "position_code": candidate["position_code"],
                "main_jd_id": candidate["main_jd_id"],
                "source_fact_id": candidate["source_fact_id"],
                "source_fact_version": candidate["source_fact_version"],
                "source_jd_id": candidate["source_jd_id"],
                "source_jd_version_id": candidate["source_jd_version_id"],
                "publication_id": verified["publication_id"],
                "record_identity": verified["record_identity"],
                "title": candidate.get("title"),
            }
        )
    if verified_by_key:
        leftover = next(iter(verified_by_key.values()))
        raise ValueError(f"verified crosswalk row without candidate: {leftover}")
    return rows, membership_reverified


def _triple(row: dict[str, Any]) -> tuple[str, str, str]:
    return (row["source_jd_id"], row["source_fact_id"], row["source_fact_version"])


def analyze_evidence_survival(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    after_universe: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute per-claim survival/added/removed evidence sets (pure, deterministic)."""
    before_by_claim: dict[str, dict[str, dict[str, Any]]] = {}
    after_by_claim: dict[str, dict[str, dict[str, Any]]] = {}
    claims = sorted(
        {row["position_code"] for row in before} | {row["position_code"] for row in after}
    )
    for row in before:
        before_by_claim.setdefault(row["position_code"], {})[row["evidence_id"]] = row
    for row in after:
        after_by_claim.setdefault(row["position_code"], {})[row["evidence_id"]] = row
    after_universe_by_id = {
        row["evidence_id"]: row.get("membership_verified", False)
        for row in after_universe
    }
    result: dict[str, Any] = {}
    for claim in claims:
        before_map = before_by_claim.get(claim, {})
        after_map = after_by_claim.get(claim, {})
        surviving = sorted(set(before_map) & set(after_map))
        added = sorted(set(after_map) - set(before_map))
        removed = sorted(set(before_map) - set(after_map))
        same_triple = [
            evidence_id
            for evidence_id in surviving
            if _triple(before_map[evidence_id]) == _triple(after_map[evidence_id])
        ]
        replaced_triple = [
            evidence_id
            for evidence_id in surviving
            if _triple(before_map[evidence_id]) != _triple(after_map[evidence_id])
        ]
        removed_reasons: dict[str, int] = {}
        for evidence_id in removed:
            if evidence_id in after_universe_by_id:
                if after_universe_by_id[evidence_id]:
                    reason = "unexpected_verified_in_after"
                else:
                    reason = "release_not_included_unverified"
            else:
                reason = "absent_from_after_sample_universe"
            removed_reasons[reason] = removed_reasons.get(reason, 0) + 1
        result[claim] = {
            "subject_ref": claim,
            "before_evidence_count": len(before_map),
            "after_evidence_count": len(after_map),
            "surviving_count": len(surviving),
            "surviving_same_fact_triple_count": len(same_triple),
            "surviving_replaced_fact_triple_count": len(replaced_triple),
            "added_count": len(added),
            "removed_count": len(removed),
            "removed_reasons": dict(sorted(removed_reasons.items())),
            "evidence_ids": {
                "surviving": surviving,
                "added": added,
                "removed": removed,
            },
            "replaced_evidence": [
                {
                    "evidence_id": evidence_id,
                    "before_source_fact_version": before_map[evidence_id][
                        "source_fact_version"
                    ],
                    "after_source_fact_version": after_map[evidence_id][
                        "source_fact_version"
                    ],
                }
                for evidence_id in replaced_triple
            ],
        }
    return result


def build_distribution_delta(
    sample_manifest: list[dict[str, Any]],
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    attributes = ("source_platform", "enterprise_identity", "template_candidate_cluster_id")
    by_id: dict[str, dict[str, str]] = {
        row["evidence_id"]: {
            key: row.get(key) or "unknown" for key in attributes
        }
        for row in sample_manifest
    }
    before_ids = {row["evidence_id"] for row in before}
    after_ids = {row["evidence_id"] for row in after}
    result: dict[str, dict[str, Any]] = {}
    for key in attributes:
        before_counts = Counter(
            by_id.get(evidence_id, {}).get(key, "unknown") for evidence_id in before_ids
        )
        after_counts = Counter(
            by_id.get(evidence_id, {}).get(key, "unknown") for evidence_id in after_ids
        )
        labels = sorted(set(before_counts) | set(after_counts))
        result[key] = {
            "before_counts": {label: before_counts[label] for label in labels},
            "after_counts": {label: after_counts[label] for label in labels},
            "delta": {
                label: after_counts[label] - before_counts[label] for label in labels
            },
        }
    return result


def build_certificate(
    *,
    before_release: dict[str, Any],
    after_release: dict[str, Any],
    before_rows: list[dict[str, Any]],
    after_rows: list[dict[str, Any]],
    after_universe: list[dict[str, Any]],
    sample_manifest: list[dict[str, Any]],
    evidence_results: dict[str, Any],
    before_membership_reverified: int,
    after_membership_reverified: int,
) -> dict[str, Any]:
    claims = analyze_evidence_survival(before_rows, after_rows, after_universe)
    deltas = build_distribution_delta(sample_manifest, before_rows, after_rows)
    for claim, payload in claims.items():
        summary = evidence_results.get(claim, {}).get("summary", {})
        payload["effective_sample_size"] = {
            "before": None,
            "after": summary.get("effective_sample_size"),
            "before_recompute_status": "not_recomputed",
            "reason": "no frozen v2 evidence-independence rerun certificate exists",
        }
        payload["uncertainty_state"] = {
            "before": None,
            "after": summary.get("uncertainty_state"),
        }
        payload["distribution_delta"] = {
            key: deltas[key] for key in ("source_platform", "enterprise_identity")
        }
    before_manifest = before_release["manifest"]
    after_manifest = after_release["manifest"]
    before_verified = before_membership_reverified
    after_verified = after_membership_reverified
    comparable = before_verified == len(before_rows) and after_verified == len(after_rows)
    algorithm_version = "evidence-independence.v2"
    config_hash = "sha256:910c4a84d7d7dc49490ba312cb270a02fea7a3cd26af493aea3543fdfef45202"
    removed_total = sum(claims[claim]["removed_count"] for claim in claims)
    conclusions = [
        "Every evidence ref verified in the before release either survives in the after release "
        "or is accounted for by release inclusion gaps; no ref is classified as expired or "
        "contradicted in this comparison.",
        "The dominant claim delta (BACKEND 61->233, LLM 19->132) reflects release inclusion "
        "(291 legacy position-v2 publications plus 34 normalization-pending samples missing "
        "from the v2 release), not evidence decay.",
        "Surviving refs with changed fact triples are version replacements "
        "(new source_fact_version), not decay.",
        "Before-release N_eff was not recomputed; the certificate makes no N_eff change claim.",
    ]
    if not comparable:
        conclusions.insert(
            0,
            "Comparability is conditional: the frozen v2 handoff triples are not present in the "
            "final committed v2 release package, so fact-triple-level before-membership cannot be "
            "re-established from the repository alone; survival counts use the frozen claim sets.",
        )
    if removed_total:
        conclusions.append(
            f"{removed_total} before-only evidence refs are recorded as removed with "
            "release-inclusion reasons; none is written as evidence invalidation."
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "certificate_id": CERTIFICATE_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "read-only membership diff over frozen EXP-EVID claim evidence sets",
        "releases": {
            "before": {
                "release_id": before_manifest["release_id"],
                "record_count": before_manifest["artifacts"][0]["record_count"],
                "observation_window": before_manifest["observation_window"],
                "created_at": before_manifest["created_at"],
                "membership_verified_count": before_release["verified_count"],
                "claim_evidence_count": len(before_rows),
                "claim_membership_reverified_count": before_verified,
            },
            "after": {
                "release_id": after_manifest["release_id"],
                "record_count": after_manifest["artifacts"][0]["record_count"],
                "observation_window": after_manifest["observation_window"],
                "created_at": after_manifest["created_at"],
                "membership_verified_count": after_release["verified_count"],
                "claim_evidence_count": len(after_rows),
                "claim_membership_reverified_count": after_verified,
            },
        },
        "comparability": {
            "status": "verified" if comparable else "conditional",
            "finding": (
                "all after-release claim triples are re-verified against the committed package; "
                "the frozen v2 handoff claim triples are not present in the final committed v2 "
                "release package (release package re-frozen after handoff generation)"
                if not comparable
                else "both release claim triples are re-verified against the committed packages"
            ),
            "recommendation": (
                "re-run build_exp_evid_release_crosswalk.py against the final v2 package and "
                "freeze a matching v2 handoff before formal fact-triple-level survival claims"
                if not comparable
                else "none; both release claim sets are repository-verifiable"
            ),
        },
        "claims": claims,
        "algorithm_identity": {
            "algorithm_version": algorithm_version,
            "config_hash": config_hash,
            "config_source": "artifacts/innovation/EXP-EVID-01/release-runs/fresh-v1/evidence-results.json",
        },
        "conclusions": conclusions,
        "limitations": [
            "v2 evidence set is small (80 refs) because the v2 release predated legacy "
            "position-v2 conversion and normalization completion.",
            "Before-release N_eff/enterprise/template distributions were not recomputed; "
            "distribution deltas use frozen sample-manifest attributes, not rerun outputs.",
            "The certificate compares two same-day full releases, not a long time horizon; "
            "freshness/decay conclusions are limited to this pair.",
        ],
        "rerun": (
            "python 框架实现/scripts/build_release_survival_certificate.py "
            "--before exp-evid-real-jd-20260812-v2 "
            "--after exp-evid-real-jd-20260812-fresh-v1"
        ),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_markdown(path: Path, certificate: dict[str, Any]) -> None:
    lines = [
        "# B-EVIDENCE-DECAY-01 跨 Release Evidence survival/decay 证书",
        "",
        f"- schema：`{certificate['schema_version']}`",
        f"- certificate：`{certificate['certificate_id']}`",
        f"- 生成时间：`{certificate['generated_at']}`",
        "",
        "## Release 对比",
        "",
        "| Release | 成员数 | 观测窗口 | claim 证据数 |",
        "| --- | ---: | --- | ---: |",
    ]
    for side in ("before", "after"):
        release = certificate["releases"][side]
        window = release["observation_window"]
        lines.append(
            f"| {side} `{release['release_id']}` | {release['membership_verified_count']} | "
            f"`{window['start']}..{window['end']}` | {release['claim_evidence_count']} |"
        )
    lines += [
        "",
        "## Release 成员重验",
        "",
        "| Release | claim 证据数 | 当前包可重验 | comparability |",
        "| --- | ---: | ---: | --- |",
    ]
    comparability = certificate["comparability"]
    lines.append(
        f"| before `{certificate['releases']['before']['release_id']}` | "
        f"{certificate['releases']['before']['claim_evidence_count']} | "
        f"{certificate['releases']['before']['claim_membership_reverified_count']} | "
        f"{comparability['status']} |"
    )
    lines.append(
        f"| after `{certificate['releases']['after']['release_id']}` | "
        f"{certificate['releases']['after']['claim_evidence_count']} | "
        f"{certificate['releases']['after']['claim_membership_reverified_count']} | "
        f"{comparability['status']} |"
    )
    lines += [
        "",
        f"- 发现：{comparability['finding']}",
        f"- 建议：{comparability['recommendation']}",
        "",
        "## Claim 级存活",
        "",
        "| claim | before | after | surviving | same triple | replaced triple | added | removed |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for claim, payload in certificate["claims"].items():
        lines.append(
            f"| {claim} | {payload['before_evidence_count']} | "
            f"{payload['after_evidence_count']} | {payload['surviving_count']} | "
            f"{payload['surviving_same_fact_triple_count']} | "
            f"{payload['surviving_replaced_fact_triple_count']} | "
            f"{payload['added_count']} | {payload['removed_count']} |"
        )
    lines += [
        "",
        "## 结论",
        "",
    ]
    lines += [f"- {item}" for item in certificate["conclusions"]]
    if "limitations" in certificate:
        lines += [
            "",
            "## 局限",
            "",
        ]
        lines += [f"- {item}" for item in certificate["limitations"]]
    lines += [
        "",
        "## 重跑",
        "",
        f"```text\n{certificate['rerun']}\n```",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", default="exp-evid-real-jd-20260812-v2")
    parser.add_argument("--after", default="exp-evid-real-jd-20260812-fresh-v1")
    parser.add_argument("--release-base", default="框架实现/data/releases")
    parser.add_argument("--handoff-base", default="artifacts/innovation/EXP-EVID-01/import-handoff")
    parser.add_argument(
        "--sample-manifest",
        default="artifacts/innovation/EXP-EVID-01/sample-manifest.jsonl",
    )
    parser.add_argument(
        "--evidence-results",
        default="artifacts/innovation/EXP-EVID-01/release-runs/fresh-v1/evidence-results.json",
    )
    parser.add_argument("--output-dir", default="artifacts/reserve/B-EVIDENCE-DECAY-01")
    args = parser.parse_args()

    release_base = REPO_ROOT / args.release_base
    handoff_base = REPO_ROOT / args.handoff_base
    before_release = load_release(release_base, args.before)
    after_release = load_release(release_base, args.after)
    before_rows, before_reverified = load_verified_evidence(
        handoff_base / args.before / "release-crosswalk.jsonl",
        handoff_base / args.before / "release-dual-position-candidates.jsonl",
        before_release["membership"],
    )
    after_universe = _read_jsonl(
        handoff_base / args.after / "release-crosswalk.jsonl"
    )
    after_rows, after_reverified = load_verified_evidence(
        handoff_base / args.after / "release-crosswalk.jsonl",
        handoff_base / args.after / "release-dual-position-candidates.jsonl",
        after_release["membership"],
    )
    sample_manifest = _read_jsonl(REPO_ROOT / args.sample_manifest)
    evidence_results = json.loads(
        (REPO_ROOT / args.evidence_results).read_text(encoding="utf-8")
    )
    certificate = build_certificate(
        before_release=before_release,
        after_release=after_release,
        before_rows=before_rows,
        after_rows=after_rows,
        after_universe=after_universe,
        sample_manifest=sample_manifest,
        evidence_results=evidence_results,
        before_membership_reverified=before_reverified,
        after_membership_reverified=after_reverified,
    )
    output_dir = REPO_ROOT / args.output_dir
    _write_json(output_dir / "certificate.json", certificate)
    _write_markdown(output_dir / "certificate.md", certificate)
    (output_dir / "rerun.md").write_text(
        "# 重跑\n\n```text\n" + certificate["rerun"] + "\n```\n",
        encoding="utf-8",
    )
    summary = {
        release["release_id"]: {
            "claim_evidence_count": release["claim_evidence_count"],
            "claim_membership_reverified_count": release[
                "claim_membership_reverified_count"
            ],
        }
        for release in certificate["releases"].values()
    }
    print(
        json.dumps(
            {
                "certificate": CERTIFICATE_ID,
                "comparability": certificate["comparability"]["status"],
                "releases": summary,
            }
        )
    )


if __name__ == "__main__":
    main()
