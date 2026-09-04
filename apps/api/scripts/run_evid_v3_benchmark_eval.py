"""Evaluate evidence-independence v3 / v3.1 on EVID-PAIR-HARD-v3 holdout.

Reports Pairwise/B-cubed with denominators, bootstrap 95% CI, FP/FN rates and
an explicit failure taxonomy.  Positive scarcity in the frozen real data is
reported as a coverage limitation, never fixed by re-labelling.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Sequence

from ai_review_protocol import load_frozen_gold
from build_evid_ai_review_metrics import (
    _b_cubed_scores,
    _pair_evidence_ids,
    _pairwise_scores,
    _read_jsonl,
    _record_from_row,
    _rows_by_position,
)
from app.contexts.evidence_independence.application import (
    build_independent_clusters_v3,
    build_independent_clusters_v3_1_with_decisions,
    build_independent_clusters_v3_2,
    _strong_identity,
)


POSITIONS = ("BACKEND_ENGINEER", "LLM_ALGORITHM_ENGINEER")
BOOTSTRAP_ITERATIONS = 2000
SEED = 20260815


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--gold",
        default=str(
            Path(__file__).resolve().parents[2]
            / "artifacts"
            / "innovation"
            / "EXP-EVID-01"
            / "pair-benchmark-v3"
            / "ai-reviewed-cluster-gold.v3.json"
        ),
    )
    parser.add_argument(
        "--formal-manifest",
        default=str(
            Path(__file__).resolve().parents[2]
            / "artifacts"
            / "innovation"
            / "EXP-EVID-01"
            / "release-runs"
            / "fresh-v1"
            / "formal-sample-manifest.jsonl"
        ),
    )
    parser.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parents[2]
            / "artifacts"
            / "innovation"
            / "EXP-EVID-01"
            / "pair-benchmark-v3"
        ),
    )
    args = parser.parse_args(argv)

    gold = load_frozen_gold(Path(args.gold))
    rows = _read_jsonl(Path(args.formal_manifest))
    rows_by_position = _rows_by_position(rows)
    positions: dict[str, dict] = {}
    for position in POSITIONS:
        position_rows = rows_by_position.get(position, [])
        records = tuple(_record_from_row(row) for row in position_rows)
        items, abstained = _gold_items(gold, position)
        if not items:
            positions[position] = {
                "reviewed_pairs": 0,
                "abstained_pairs": abstained,
                "note": "no decided holdout pairs for this position",
            }
            continue
        v3_clusters = build_independent_clusters_v3(records)
        v31_clusters, v31_decisions = build_independent_clusters_v3_1_with_decisions(
            records
        )
        v3_metrics = _metric_payload(
            items, abstained, _cluster_map(v3_clusters), len(position_rows)
        )
        v31_metrics = _metric_payload(
            items,
            abstained,
            _cluster_map(v31_clusters),
            len(position_rows),
            pair_decisions=_decision_map(v31_decisions),
        )
        v32_clusters, v32_certificates = build_independent_clusters_v3_2(
            records
        )
        cluster_audit = _cluster_audit(records, v32_clusters)
        v32_metrics = _metric_payload(
            items,
            abstained,
            _cluster_map(v32_clusters),
            len(position_rows),
            pair_decisions=_certificate_map(v32_certificates),
            certificate_count=len(v32_certificates),
            merge_accepted_count=sum(
                1
                for certificate in v32_certificates
                if certificate.union_accepted is True
            ),
            review_rejection_count=sum(
                1
                for certificate in v32_certificates
                if certificate.final_decision == "review_required"
            ),
        )
        positions[position] = {
            "reviewed_pairs": len(items),
            "abstained_pairs": abstained,
            "positive_pairs": sum(1 for item in items if item[2] is True),
            "negative_pairs": sum(1 for item in items if item[2] is False),
            "v3": v3_metrics,
            "v3_1": v31_metrics,
            "v3_2": v32_metrics,
            "v3_2_cluster_audit": cluster_audit,
            "failure_taxonomy_v3": _failure_taxonomy(
                items, _cluster_map(v3_clusters), gold, position
            ),
            "failure_taxonomy_v3_2": _failure_taxonomy(
                items, _cluster_map(v32_clusters), gold, position
            ),
        }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "evid-pair-hard-v3-eval.v2",
        "experiment_id": "EXP-EVID-01",
        "benchmark": "EVID-PAIR-HARD-v3",
        "gold": str(gold.get("gold_version")),
        "is_human_gold": False,
        "abstain_excluded": True,
        "bootstrap": {
            "iterations": BOOTSTRAP_ITERATIONS,
            "seed": SEED,
        },
        "positions": positions,
    }
    (out / "eval-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out / "TAB-EVID-PAIR-HARD.md").write_text(
        _render_table(report), encoding="utf-8"
    )
    print(out / "eval-report.json")
    return 0


def _gold_items(
    gold: dict, position: str
) -> tuple[list[tuple[str, str, object, str]], int]:
    items: list[tuple[str, str, object, str]] = []
    abstained = 0
    review = gold.get("review", {}).get("items", {})
    pack_items = (gold.get("pack") or {}).get("items", {})
    for pair_id, record in review.items():
        pack_item = pack_items.get(pair_id)
        if pack_item is None or pack_item.get("position") != position:
            continue
        if record.get("final_label") == "abstain":
            abstained += 1
            continue
        left_id, right_id = _pair_evidence_ids(str(pair_id))
        if left_id is None or right_id is None:
            continue
        items.append((left_id, right_id, record["final_value"], str(pair_id)))
    return items, abstained


def _metric_payload(
    items: list[tuple[str, str, object, str]],
    abstained: int,
    cluster_by_id: dict[str, str],
    sample_count: int,
    pair_decisions: dict[tuple[str, str], str] | None = None,
    *,
    certificate_count: int | None = None,
    merge_accepted_count: int | None = None,
    review_rejection_count: int | None = None,
) -> dict:
    positive_pairs = [item for item in items if item[2] is True]
    negative_pairs = [item for item in items if item[2] is False]
    positive_tp = sum(
        1
        for left, right, _same, _pair_id in positive_pairs
        if cluster_by_id.get(left) == cluster_by_id.get(right)
    )
    negative_tn = sum(
        1
        for left, right, _same, _pair_id in negative_pairs
        if cluster_by_id.get(left) != cluster_by_id.get(right)
    )
    pairwise = _pairwise_scores(
        positive_pairs, negative_pairs, positive_tp, negative_tn
    )
    b_cubed = _b_cubed_scores(items, cluster_by_id)
    selective = _selective_payload(
        items,
        pair_decisions,
        certificate_count=certificate_count,
        merge_accepted_count=merge_accepted_count,
        review_rejection_count=review_rejection_count,
    )
    return {
        "reviewed_pairs": len(items),
        "abstained_pairs": abstained,
        "sample_count": sample_count,
        "pairwise": pairwise,
        "b_cubed": b_cubed,
        "selective": selective,
        "fp_per_100_independent": (
            round(
                (len(negative_pairs) - negative_tn)
                * 100.0
                / max(len(negative_pairs), 1),
                2,
            )
        ),
        "fn_per_100_dependent": (
            round(
                (len(positive_pairs) - positive_tp)
                * 100.0
                / max(len(positive_pairs), 1),
                2,
            )
        ),
        "bootstrap": _bootstrap_ci(items, cluster_by_id),
    }


def _decision_map(
    pair_decisions: tuple[tuple[str, str, str], ...],
) -> dict[tuple[str, str], str]:
    return {
        (left_id, right_id): decision
        for left_id, right_id, decision in pair_decisions
    }


def _certificate_map(certificates) -> dict[tuple[str, str], str]:
    return {
        (certificate.left_evidence_id, certificate.right_evidence_id): (
            certificate.final_decision
        )
        for certificate in certificates
    }


def _selective_payload(
    items: list[tuple[str, str, object, str]],
    pair_decisions: dict[tuple[str, str], str] | None,
    *,
    certificate_count: int | None = None,
    merge_accepted_count: int | None = None,
    review_rejection_count: int | None = None,
) -> dict | None:
    """Safety metrics: auto-merge precision/recall + dependent coverage."""

    if not pair_decisions:
        return None
    decisions = [
        (left, right, same, pair_decisions.get((left, right), "independent"))
        for left, right, same, _pair_id in items
    ]
    total = len(decisions)
    review_required = [
        item for item in decisions if item[3] == "review_required"
    ]
    positives = [item for item in decisions if item[2] is True]
    distribution = {
        decision: sum(1 for item in decisions if item[3] == decision)
        for decision in ("merge", "review_required", "independent")
    }
    auto_merged = [
        item for item in decisions if item[3] == "merge"
    ]
    review_required = [
        item for item in decisions if item[3] == "review_required"
    ]
    auto_merge_tp = sum(
        1 for item in auto_merged if item[2] is True
    )
    auto_merge_fp = sum(
        1 for item in auto_merged if item[2] is False
    )
    positive_count = len(positives)
    auto_merge_recall = (
        round(auto_merge_tp / positive_count, 6)
        if positive_count
        else None
    )
    auto_merge_precision = (
        round(
            auto_merge_tp / (auto_merge_tp + auto_merge_fp),
            6,
        )
        if (auto_merge_tp + auto_merge_fp)
        else None
    )
    dependent_captured = sum(
        1
        for item in decisions
        if item[2] is True and item[3] in {"merge", "review_required"}
    )
    review_captured = sum(
        1 for item in review_required if item[2] is True
    )
    not_auto_merged_positives = positive_count - auto_merge_tp
    return {
        "review_rate": round(len(review_required) / total, 6) if total else None,
        "review_positive_density": (
            round(
                review_captured
                / len(review_required),
                6,
            )
            if review_required
            else None
        ),
        "auto_merge_precision": auto_merge_precision,
        "auto_merge_recall": auto_merge_recall,
        "dependent_coverage": (
            round(dependent_captured / positive_count, 6)
            if positive_count
            else None
        ),
        "review_capture_recall": (
            round(
                review_captured / not_auto_merged_positives,
                6,
            )
            if not_auto_merged_positives
            else None
        ),
        "selective_recall_deprecated": (
            round(review_captured / positive_count, 6)
            if positive_count
            else None
        ),
        "decision_distribution": distribution,
        "certificate_count": certificate_count,
        "merge_accepted_count": merge_accepted_count,
        "review_rejection_count": review_rejection_count,
    }


def _bootstrap_ci(
    items: list[tuple[str, str, object, str]],
    cluster_by_id: dict[str, str],
) -> dict:
    rng = random.Random(SEED)
    f1_values: list[float] = []
    if not items:
        return {"pairwise_f1_ci": None}
    for _iteration in range(BOOTSTRAP_ITERATIONS):
        sample = [rng.choice(items) for _ in range(len(items))]
        positive_pairs = [item for item in sample if item[2] is True]
        negative_pairs = [item for item in sample if item[2] is False]
        positive_tp = sum(
            1
            for left, right, _same, _pair_id in positive_pairs
            if cluster_by_id.get(left) == cluster_by_id.get(right)
        )
        negative_tn = sum(
            1
            for left, right, _same, _pair_id in negative_pairs
            if cluster_by_id.get(left) != cluster_by_id.get(right)
        )
        pairwise = _pairwise_scores(
            positive_pairs, negative_pairs, positive_tp, negative_tn
        )
        if pairwise["f1"] is not None:
            f1_values.append(pairwise["f1"])
    if not f1_values:
        return {"pairwise_f1_ci": None}
    f1_values.sort()
    lower = f1_values[int(0.025 * len(f1_values))]
    upper = f1_values[int(0.975 * len(f1_values)) - 1]
    return {
        "pairwise_f1_ci": [round(lower, 4), round(upper, 4)],
        "median_f1": round(f1_values[len(f1_values) // 2], 4),
    }


def _failure_taxonomy(
    items: list[tuple[str, str, object, str]],
    cluster_by_id: dict[str, str],
    gold: dict,
    position: str,
) -> dict:
    taxonomy = {
        "template_false_merge": 0,
        "same_company_false_merge": 0,
        "timestamp_missing_false_split": 0,
        "cross_source_false_split": 0,
        "semantic_ambiguity": 0,
    }
    pack_items = (gold.get("pack") or {}).get("items", {})
    for left_id, right_id, same, pair_id in items:
        predicted_same = cluster_by_id.get(left_id) == cluster_by_id.get(right_id)
        pack_item = pack_items.get(pair_id) or {}
        left = pack_item.get("left") or {}
        right = pack_item.get("right") or {}
        if same is False and predicted_same:
            if (
                left.get("template_cluster")
                and left.get("template_cluster") == right.get("template_cluster")
            ):
                taxonomy["template_false_merge"] += 1
            elif (
                left.get("enterprise")
                and left.get("enterprise") == right.get("enterprise")
            ):
                taxonomy["same_company_false_merge"] += 1
            else:
                taxonomy["semantic_ambiguity"] += 1
        elif same is True and not predicted_same:
            if not left.get("published_at") or not right.get("published_at"):
                taxonomy["timestamp_missing_false_split"] += 1
            elif left.get("source") != right.get("source"):
                taxonomy["cross_source_false_split"] += 1
            else:
                taxonomy["semantic_ambiguity"] += 1
    return taxonomy


def _cluster_map(clusters: tuple[tuple[str, ...], ...]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for cluster in clusters:
        for evidence_id in cluster:
            mapping[evidence_id] = cluster[0]
    return mapping


def _cluster_audit(
    records: Sequence,
    clusters: tuple[tuple[str, ...], ...],
) -> dict:
    record_by_id = {record.evidence_id: record for record in records}
    multi = [cluster for cluster in clusters if len(cluster) > 1]
    cross_enterprise = 0
    cross_enterprise_without_strong_identity = 0
    for cluster in multi:
        members = [record_by_id[evidence_id] for evidence_id in cluster]
        enterprises = {
            record_by_id[evidence_id].enterprise_id
            for evidence_id in cluster
            if record_by_id[evidence_id].enterprise_id
        }
        if len(enterprises) > 1:
            cross_enterprise += 1
            if not any(
                _strong_identity(left, right)
                for left in members
                for right in members
                if left.evidence_id < right.evidence_id
            ):
                cross_enterprise_without_strong_identity += 1
    return {
        "cluster_count": len(clusters),
        "multi_record_cluster_count": len(multi),
        "cross_enterprise_cluster_count": cross_enterprise,
        "cross_enterprise_without_strong_identity": (
            cross_enterprise_without_strong_identity
        ),
        "false_merge_free": cross_enterprise_without_strong_identity == 0,
    }


def _display(value) -> str:
    return "-" if value is None else f"{value:.4f}"


def _render_table(report: dict) -> str:
    lines = [
        "# TAB-EVID-PAIR-HARD EVID-PAIR-HARD-v3 holdout evaluation",
        "",
        "- Gold：`ai-reviewed-cluster-gold.v3`（live 两轮 + 仲裁，AI proxy）",
        "- 说明：正样本稀缺来自冻结真实数据，不重新标注。",
        "- 安全指标：`auto_merge_precision` = 自动 merge 中 gold positive "
        "占比；`dependent_coverage` = positive 被 merge 或 review 捕获的比例；"
        "`review_capture_recall` = 未自动 merge 的 positive 被 review 捕获的比例。",
        "",
        "| 岗位 | 版本 | pairs | TP | FP | FN | TN | Precision | Recall | F1 | B-cubed | F1 CI |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for position, payload in report["positions"].items():
        if "v3" not in payload:
            lines.append(f"| {position} | - | 0 | - | - | - | - | - | - | - | - | - |")
            continue
        for version in ("v3", "v3_1", "v3_2"):
            metric = payload[version]
            pw = metric["pairwise"]
            ci = (metric.get("bootstrap") or {}).get("pairwise_f1_ci")
            ci_text = (
                "-"
                if ci is None
                else f"[{ci[0]:.2f}, {ci[1]:.2f}]"
            )
            lines.append(
                "| {pos} | {ver} | {pairs} | {tp} | {fp} | {fn} | {tn} | "
                "{prec} | {rec} | {f1} | {bc} | {ci} |".format(
                    pos=position,
                    ver=version,
                    pairs=metric["reviewed_pairs"],
                    tp=pw["true_positive"],
                    fp=pw["false_positive"],
                    fn=pw["false_negative"],
                    tn=pw["true_negative"],
                    prec=_display(pw["precision"]),
                    rec=_display(pw["recall"]),
                    f1=_display(pw["f1"]),
                    bc=_display(metric["b_cubed"]["f1"]),
                    ci=ci_text,
                )
            )
        for label, version_key in (("v3.1", "v3_1"), ("v3.2", "v3_2")):
            selective = (payload.get(version_key) or {}).get("selective")
            if selective is None:
                continue
            lines.append("")
            lines.append(f"### {position} {label} calibrated abstention")
            lines.append(
                "| review_rate | auto-merge precision | auto-merge recall | "
                "dependent coverage | review capture recall | merge | "
                "review_required | independent | certificate | "
                "merge_accepted | rejection |"
            )
            lines.append(
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
            )
            distribution = selective.get("decision_distribution") or {}
            lines.append(
                "| {rate} | {merge_p} | {merge_r} | {coverage} | "
                "{capture} | {merge} | {review} | {independent} | "
                "{certificate} | {accepted} | {rejection} |".format(
                    rate=_display(selective.get("review_rate")),
                    merge_p=_display(selective.get("auto_merge_precision")),
                    merge_r=_display(selective.get("auto_merge_recall")),
                    coverage=_display(selective.get("dependent_coverage")),
                    capture=_display(selective.get("review_capture_recall")),
                    merge=distribution.get("merge", 0),
                    review=distribution.get("review_required", 0),
                    independent=distribution.get("independent", 0),
                    certificate=selective.get("certificate_count") or "-",
                    accepted=selective.get("merge_accepted_count") or "-",
                    rejection=selective.get("review_rejection_count") or "-",
                )
            )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
