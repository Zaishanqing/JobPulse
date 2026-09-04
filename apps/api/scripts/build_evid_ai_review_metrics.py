"""Build AI-reviewed proxy metrics for EXP-EVID-01.

Reads the frozen ``ai-reviewed-cluster-gold.v1`` and the formal sample
manifest, then computes Pairwise F1 and B-cubed F1 on the reviewed pair
subgraph.  All metrics are labeled ``ai_reviewed_proxy``; abstained pairs are
excluded; the output is explicitly not human Gold.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_review_protocol import load_frozen_gold


POSITIONS = ("BACKEND_ENGINEER", "LLM_ALGORITHM_ENGINEER")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--gold",
        default=str(
            Path(__file__).resolve().parents[2]
            / "artifacts"
            / "innovation"
            / "EXP-EVID-01"
            / "ai-review"
            / "ai-reviewed-cluster-gold.v1.json"
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
        ),
    )
    args = parser.parse_args(argv)

    gold = load_frozen_gold(Path(args.gold))
    rows = _read_jsonl(Path(args.formal_manifest))
    rows_by_position = _rows_by_position(rows)
    cluster_by_id = _algorithm_clusters_by_position(rows_by_position)
    metrics: dict[str, dict] = {}
    for position in POSITIONS:
        position_rows = rows_by_position.get(position, [])
        items, abstained = _position_items(gold, position)
        metrics[position] = _position_metrics(
            position, items, abstained, cluster_by_id, len(position_rows)
        )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "exp-evid-ai-review-metrics.v1",
        "experiment_id": "EXP-EVID-01",
        "gold_version": str(gold.get("gold_version")),
        "gold_type": "ai_reviewed_cluster_proxy",
        "is_human_gold": False,
        "abstain_excluded": True,
        "release_id": "exp-evid-real-jd-20260812-fresh-v1",
        "judge_agreement_rate": (
            gold.get("review", {}).get("judge_agreement_rate")
        ),
        "positions": metrics,
    }
    (out_dir / "ai-review" / "ai-review-metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out_dir / "tables" / "TAB-EVID-02.md").write_text(
        _render_table(report), encoding="utf-8"
    )
    (out_dir / "tables" / "TAB-EVID-02.json").write_text(
        json.dumps(
            {
                "experiment_id": "EXP-EVID-01",
                "table_id": "TAB-EVID-02",
                "gold_type": "ai_reviewed_cluster_proxy",
                "rows": [
                    {
                        "position": position,
                        "reviewed_pairs": metrics[position]["reviewed_pairs"],
                        "decided_pairs": metrics[position]["decided_pairs"],
                        "abstained_pairs": metrics[position]["abstained_pairs"],
                        "pairwise_f1": metrics[position]["pairwise"]["f1"],
                        "b_cubed_f1": metrics[position]["b_cubed"]["f1"],
                    }
                    for position in POSITIONS
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(out_dir / "ai-review" / "ai-review-metrics.json")
    return 0


def _position_items(
    gold: dict, position: str
) -> tuple[list[tuple[str, str, object, object]], int]:
    items: list[tuple[str, str, object, object]] = []
    abstained = 0
    review = gold.get("review", {}).get("items", {})
    pack_items = (gold.get("pack") or {}).get("items", {})
    for pair_id, record in review.items():
        pack_item = pack_items.get(pair_id)
        if pack_item is None:
            continue
        if pack_item.get("position") != position:
            continue
        if record.get("final_label") == "abstain":
            abstained += 1
            continue
        left_id, right_id = _pair_evidence_ids(str(pair_id))
        if left_id is None or right_id is None:
            continue
        if not str(pair_id).startswith(("same:", "cross:")):
            continue
        items.append((left_id, right_id, record["final_value"], pair_id))
    return items, abstained


def _pair_evidence_ids(pair_id: str) -> tuple[str | None, str | None]:
    if pair_id.startswith("same:"):
        return _split_evidence_pair(pair_id, prefix="same:")
    if pair_id.startswith("cross:"):
        return _split_evidence_pair(pair_id, prefix="cross:")
    return None, None


def _split_evidence_pair(
    pair_id: str, *, prefix: str
) -> tuple[str | None, str | None]:
    rest = pair_id[len(prefix):]
    # Evidence ids themselves contain ":" (e.g. exp-evid:xxxx:yyyy), so the
    # pair separator is the second colon of the remainder.
    first_sep = rest.find(":")
    if first_sep < 0:
        return None, None
    second_sep = rest.find(":", first_sep + 1)
    if second_sep < 0:
        return None, None
    left = rest[:second_sep]
    right = rest[second_sep + 1:]
    return left, right


def _position_metrics(
    position: str,
    items: list[tuple[str, str, object, object]],
    abstained: int,
    cluster_by_id: dict[str, str],
    sample_count: int,
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
    return {
        "sample_count": sample_count,
        "reviewed_pairs": len(items),
        "decided_pairs": len(items),
        "abstained_pairs": abstained,
        "positive_pairs": len(positive_pairs),
        "negative_pairs": len(negative_pairs),
        "pairwise": pairwise,
        "b_cubed": b_cubed,
    }


def _pairwise_scores(
    positive_pairs: list[tuple],
    negative_pairs: list[tuple],
    positive_tp: int,
    negative_tn: int,
) -> dict:
    positive_fn = len(positive_pairs) - positive_tp
    negative_fp = len(negative_pairs) - negative_tn
    precision = _fraction(positive_tp, positive_tp + negative_fp)
    recall = _fraction(positive_tp, positive_tp + positive_fn)
    return {
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
        "true_positive": positive_tp,
        "false_negative": positive_fn,
        "false_positive": negative_fp,
        "true_negative": negative_tn,
    }


def _b_cubed_scores(
    items: list[tuple[str, str, object, object]],
    cluster_by_id: dict[str, str],
) -> dict:
    evidence_ids = sorted(
        {evidence_id for left, right, _same, _pair_id in items for evidence_id in (left, right)}
    )
    gold_groups = _gold_components(items, evidence_ids)
    predicted = {evidence_id: cluster_by_id.get(evidence_id, evidence_id) for evidence_id in evidence_ids}
    gold_id_by_item = {
        evidence_id: group_id
        for group_id, members in enumerate(gold_groups)
        for evidence_id in members
    }
    precision = recall = 0.0
    for evidence_id in evidence_ids:
        gold_group = {item for item in evidence_ids if gold_id_by_item.get(item) == gold_id_by_item[evidence_id]}
        pred_group = {item for item in evidence_ids if predicted.get(item) == predicted[evidence_id]}
        if pred_group:
            precision += len(gold_group & pred_group) / len(pred_group)
        if gold_group:
            recall += len(gold_group & pred_group) / len(gold_group)
    if evidence_ids:
        precision /= len(evidence_ids)
        recall /= len(evidence_ids)
    return {
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": _f1(precision, recall),
    }


def _gold_components(
    items: list[tuple[str, str, object, object]],
    evidence_ids: list[str],
) -> list[set[str]]:
    parent = {evidence_id: evidence_id for evidence_id in evidence_ids}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for left, right, same, _pair_id in items:
        if same is True:
            union(left, right)
    grouped: dict[str, set[str]] = {}
    for evidence_id in evidence_ids:
        grouped.setdefault(find(evidence_id), set()).add(evidence_id)
    return list(grouped.values())


def _algorithm_clusters_by_position(
    rows_by_position: dict[str, list[dict]],
) -> dict[str, str]:
    from app.contexts.evidence_independence.application import (
        build_independent_clusters,
    )

    mapping: dict[str, str] = {}
    for position, position_rows in rows_by_position.items():
        records = [_record_from_row(row) for row in position_rows]
        clusters = build_independent_clusters(tuple(records))
        for cluster in clusters:
            for evidence_id in cluster:
                mapping[evidence_id] = cluster[0]
    return mapping


def _record_from_row(row: dict) -> object:
    from datetime import date, datetime

    from app.contexts.evidence_independence.contracts import EvidenceRecord

    return EvidenceRecord(
        evidence_id=str(row["evidence_id"]),
        subject_ref=str(row["position_code"]),
        source_id=str(row["source_platform"]),
        enterprise_id=_optional(
            row.get("enterprise_identity") or row.get("enterprise_id")
        ),
        text_fingerprint=_optional(row.get("text_fingerprint")),
        position_id=str(row["position_code"]),
        published_at=(
            date.fromisoformat(str(row["published_at"]))
            if row.get("published_at")
            else None
        ),
        collected_at=(
            datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
            if row.get("observed_at")
            else None
        ),
        template_cluster_id=_optional(row.get("template_candidate_cluster_id")),
        release_id="exp-evid-real-jd-20260812-fresh-v1",
        source_version=_optional(row.get("source_fact_version")),
        text=_optional(row.get("text_excerpt")),
    )


def _optional(value: object) -> str | None:
    return str(value) if value is not None and str(value).strip() else None


def _rows_by_position(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row["position_code"]), []).append(row)
    return grouped


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _fraction(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return round(2 * precision * recall / (precision + recall), 6)


def _render_table(report: dict) -> str:
    lines = [
        "# TAB-EVID-02 EXP-EVID-01 AI-reviewed cluster proxy metrics",
        "",
        "- Gold：`ai-reviewed-cluster-gold.v1`（AI-reviewed proxy，非人工 Gold）",
        "- 说明：Pairwise F1 与 B-cubed F1 仅在已确认的批审对子图上计算，abstain 不计入。",
        "",
        "| 岗位 | 样本 | 已审对 | 确认对 | abstain | Pairwise F1 | B-cubed F1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for position in report["positions"]:
        metric = report["positions"][position]
        lines.append(
            "| {pos} | {sample} | {reviewed} | {decided} | {abstain} | {pw} | {bc} |".format(
                pos=position,
                sample=metric["sample_count"],
                reviewed=metric["reviewed_pairs"],
                decided=metric["decided_pairs"],
                abstain=metric["abstained_pairs"],
                pw=_display(metric["pairwise"]["f1"]),
                bc=_display(metric["b_cubed"]["f1"]),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _display(value: float | None) -> str:
    return "N/A" if value is None else str(value)


if __name__ == "__main__":
    raise SystemExit(main())
