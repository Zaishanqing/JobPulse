"""EVID-PAIR-HARD-v4 evaluation with frozen AI-reviewed proxy gold.

Reports per-channel candidate yield and positive density, v3.2 selective
safety metrics, and a v3.3 semantic-assisted selective decision layer built
on top of v3.2 pair certificates plus frozen BGE-M3 similarity scores.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_review_protocol import load_frozen_gold
from build_evid_ai_review_metrics import (
    _pair_evidence_ids,
    _read_jsonl,
    _record_from_row,
    _rows_by_position,
)
from run_evid_v3_benchmark_eval import (
    _certificate_map,
    _cluster_map,
    _gold_items,
    _metric_payload,
)
from app.contexts.evidence_independence.application import (
    build_independent_clusters_v3_2,
)


POSITIONS = ("BACKEND_ENGINEER", "LLM_ALGORITHM_ENGINEER")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    benchmark_dir = (
        Path(__file__).resolve().parents[2]
        / "artifacts"
        / "innovation"
        / "EXP-EVID-01"
        / "pair-benchmark-v4"
    )
    parser.add_argument("--gold", default=str(benchmark_dir / "frozen-gold.json"))
    parser.add_argument("--pack", default=str(benchmark_dir / "benchmark-pack.json"))
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
    parser.add_argument("--out", default=str(benchmark_dir))
    parser.add_argument("--merge-similarity", type=float, default=0.75)
    parser.add_argument("--review-similarity", type=float, default=0.75)
    parser.add_argument("--independent-similarity", type=float, default=0.20)
    args = parser.parse_args(argv)

    gold = load_frozen_gold(Path(args.gold))
    pack = json.loads(Path(args.pack).read_text(encoding="utf-8"))
    pack_items = pack.get("items", {})
    rows = _read_jsonl(Path(args.formal_manifest))
    rows_by_position = _rows_by_position(rows)

    positions: dict[str, dict] = {}
    all_items: list[tuple[str, str, object, object]] = []
    all_v32_decisions: dict[tuple[str, str], str] = {}
    all_v33_decisions: dict[tuple[str, str], str] = {}
    all_cluster_by_id: dict[str, str] = {}
    total_rows = 0
    total_abstained = 0
    for position in POSITIONS:
        position_rows = rows_by_position.get(position, [])
        records = tuple(_record_from_row(row) for row in position_rows)
        items, abstained = _gold_items(gold, position)
        clusters, certificates = build_independent_clusters_v3_2(records)
        cluster_by_id = _cluster_map(clusters)
        v32_decisions = _certificate_map(certificates)
        v33_decisions = _semantic_refine_decisions(
            v32_decisions,
            pack_items,
            merge_similarity=args.merge_similarity,
            review_similarity=args.review_similarity,
            independent_similarity=args.independent_similarity,
        )
        v32_metrics = _metric_payload(
            items,
            abstained,
            cluster_by_id,
            len(position_rows),
            pair_decisions=v32_decisions,
            certificate_count=len(certificates),
            merge_accepted_count=sum(
                1 for certificate in certificates if certificate.union_accepted is True
            ),
            review_rejection_count=sum(
                1
                for certificate in certificates
                if certificate.final_decision == "review_required"
            ),
        )
        v33_metrics = _metric_payload(
            items,
            abstained,
            cluster_by_id,
            len(position_rows),
            pair_decisions=v33_decisions,
        )
        positions[position] = {
            "sample_count": len(position_rows),
            "reviewed_pairs": len(items),
            "abstained_pairs": abstained,
            "positive_pairs": sum(1 for item in items if item[2] is True),
            "negative_pairs": sum(1 for item in items if item[2] is False),
            "channel_yield": _channel_yield(gold, pack_items, position),
            "v3_2": v32_metrics,
            "v3_3": v33_metrics,
            "v3_3_channel_positive_recall": _channel_positive_recall(
                items, pack_items, v33_decisions
            ),
        }
        all_items.extend(items)
        all_v32_decisions.update(v32_decisions)
        position_pair_keys = {
            (left, right) for left, right, _same, _pair_id in items
        }
        for pair_key, decision in v33_decisions.items():
            if pair_key in position_pair_keys:
                all_v33_decisions[pair_key] = decision
        all_cluster_by_id.update(cluster_by_id)
        total_rows += len(position_rows)
        total_abstained += abstained

    overall = {
        "sample_count": total_rows,
        "reviewed_pairs": len(all_items),
        "abstained_pairs": total_abstained,
        "positive_pairs": sum(1 for item in all_items if item[2] is True),
        "negative_pairs": sum(1 for item in all_items if item[2] is False),
        "v3_2": _metric_payload(
            all_items,
            total_abstained,
            all_cluster_by_id,
            total_rows,
            pair_decisions=all_v32_decisions,
        ),
        "v3_3": _metric_payload(
            all_items,
            total_abstained,
            all_cluster_by_id,
            total_rows,
            pair_decisions=all_v33_decisions,
        ),
    }
    report = {
        "schema_version": "evid-pair-hard-v4-eval.v1",
        "experiment_id": "EXP-EVID-01",
        "benchmark": "EVID-PAIR-HARD-v4",
        "gold": str(gold.get("gold_version")),
        "is_human_gold": False,
        "abstain_excluded": True,
        "merge_similarity": args.merge_similarity,
        "review_similarity": args.review_similarity,
        "independent_similarity": args.independent_similarity,
        "overall": overall,
        "positions": positions,
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "v4-eval-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out / "TAB-EVID-PAIR-HARD-V4.md").write_text(
        _render_table(report), encoding="utf-8"
    )
    print(out / "v4-eval-report.json")
    return 0


def _semantic_refine_decisions(
    decisions: dict[tuple[str, str], str],
    pack_items: dict[str, dict],
    *,
    merge_similarity: float = 0.75,
    review_similarity: float = 0.75,
    independent_similarity: float = 0.20,
) -> dict[tuple[str, str], str]:
    refined: dict[tuple[str, str], str] = {}
    for pair_id, item in pack_items.items():
        left, right = _pair_evidence_ids(str(pair_id))
        if left is None or right is None:
            continue
        base = decisions.get((left, right), "independent")
        similarity = float(item.get("semantic_similarity") or 0.0)
        same_enterprise_position = (
            item.get("semantic_same_enterprise_position") is True
        )
        if base == "merge":
            refined[(left, right)] = "merge"
        elif base == "review_required":
            if same_enterprise_position and similarity >= merge_similarity:
                refined[(left, right)] = "merge"
            elif similarity <= independent_similarity:
                refined[(left, right)] = "independent"
            else:
                refined[(left, right)] = "review_required"
        elif similarity >= review_similarity:
            refined[(left, right)] = "review_required"
        else:
            refined[(left, right)] = "independent"
    return refined


def _channel_yield(
    gold: dict, pack_items: dict[str, dict], position: str
) -> dict[str, dict]:
    review = gold.get("review", {}).get("items", {})
    channels: dict[str, dict] = {}
    for pair_id, record in review.items():
        item = pack_items.get(pair_id)
        if item is None or item.get("position") != position:
            continue
        kind = str(item.get("source_kind") or "unknown")
        stats = channels.setdefault(
            kind,
            {
                "candidate": 0,
                "dependent": 0,
                "independent": 0,
                "abstain": 0,
                "uncertain": 0,
            },
        )
        stats["candidate"] += 1
        label = record.get("final_label")
        value = record.get("final_value")
        if label == "abstain":
            stats["abstain"] += 1
        elif value is True:
            stats["dependent"] += 1
        elif value is False:
            stats["independent"] += 1
        else:
            stats["uncertain"] += 1
    for stats in channels.values():
        stats["positive_density"] = round(
            stats["dependent"] / max(stats["candidate"], 1), 6
        )
    return dict(sorted(channels.items()))


def _channel_positive_recall(
    items: list[tuple[str, str, object, object]],
    pack_items: dict[str, dict],
    decisions: dict[tuple[str, str], str],
) -> dict[str, dict]:
    channels: dict[str, dict] = {}
    for left, right, same, pair_id in items:
        if same is not True:
            continue
        item = pack_items.get(str(pair_id))
        kind = str((item or {}).get("source_kind") or "unknown")
        stats = channels.setdefault(
            kind, {"gold_positive": 0, "captured": 0, "auto_merged": 0}
        )
        stats["gold_positive"] += 1
        decision = decisions.get((left, right), "independent")
        if decision in {"merge", "review_required"}:
            stats["captured"] += 1
        if decision == "merge":
            stats["auto_merged"] += 1
    for stats in channels.values():
        stats["dependent_capture_recall"] = round(
            stats["captured"] / max(stats["gold_positive"], 1), 6
        )
    return dict(sorted(channels.items()))


def _render_table(report: dict) -> str:
    lines = [
        "# TAB-EVID-PAIR-HARD-V4",
        "",
        f"- Gold: `{report['gold']}` (AI-reviewed proxy, not human gold)",
        f"- merge similarity: {report['merge_similarity']}",
        f"- review similarity: {report['review_similarity']}",
        f"- independent similarity: {report['independent_similarity']}",
        "",
    ]
    lines.append("| metric | v3.2 | v3.3 |")
    lines.append("|---|---:|---:|")
    overall = report["overall"]
    for label, key in (
        ("Auto-Merge Precision", "auto_merge_precision"),
        ("Auto-Merge Recall", "auto_merge_recall"),
        ("Dependent Coverage", "dependent_coverage"),
        ("Review Capture Recall", "review_capture_recall"),
        ("Review Rate", "review_rate"),
    ):
        values = [
            _fmt((overall[item].get("selective") or {}).get(key))
            for item in ("v3_2", "v3_3")
        ]
        lines.append(f"| {label} | {values[0]} | {values[1]} |")
    lines.append("")
    lines.append("## Per-channel yield (overall)")
    lines.append("| channel | candidates | dependent | independent | abstain | positive density |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    merged: dict[str, dict] = {}
    for position in report["positions"].values():
        for channel, stats in position["channel_yield"].items():
            target = merged.setdefault(
                channel,
                {"candidate": 0, "dependent": 0, "independent": 0, "abstain": 0},
            )
            for key in ("candidate", "dependent", "independent", "abstain"):
                target[key] += stats[key]
    for channel, stats in sorted(merged.items()):
        density = round(stats["dependent"] / max(stats["candidate"], 1), 6)
        lines.append(
            f"| {channel} | {stats['candidate']} | {stats['dependent']} "
            f"| {stats['independent']} | {stats['abstain']} | {density} |"
        )
    return "\n".join(lines) + "\n"


def _fmt(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
