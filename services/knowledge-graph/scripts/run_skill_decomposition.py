"""A20: 在真实快照对上运行语义/拓扑双分解，生成报告。

读取现有快照和事件对，对每对版本运行 decompose_version_pair，
输出分解详情 JSON + 可读报告 MD。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

KG_SERVICE = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(KG_SERVICE))

from app.domain.skill_decomposition import (  # noqa: E402
    decompose_version_pair,
    compute_pair_summary,
    compute_cross_pair_analysis,
)

SNAPSHOTS_DIR = KG_SERVICE / "evaluation" / "reports" / "real-graph-baseline" / "snapshots"
EVENTS_DIR = KG_SERVICE / "evaluation" / "reports" / "real-graph-baseline"
OUTPUT_DIR = KG_SERVICE / "evaluation" / "reports" / "a20-decomposition"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

POSITIONS = [
    ("POS_JAVA_BACKEND", "Java后端工程师"),
    ("POS_LLM_ALGO", "大模型算法工程师"),
]


def load_snapshots(prefix: str) -> list[tuple[str, dict]]:
    """加载某个岗位的所有快照，按时间排序。返回 [(stem, data), ...]。"""
    snaps: list[tuple[str, dict]] = []
    for sp in sorted(SNAPSHOTS_DIR.glob(f"{prefix}_v*.json")):
        data = json.loads(sp.read_text(encoding="utf-8"))
        snaps.append((sp.stem, data))
    snaps.sort(key=lambda x: x[1]["time_window"]["start"])
    return snaps


def run():
    print("=== A20: 语义/拓扑双分解 ===\n")

    all_pair_results: list[dict] = []
    all_decomps_for_cross: list[tuple[str, list]] = []

    for pid, pname in POSITIONS:
        snaps = load_snapshots(pid)
        print(f"{pname} ({pid}): {len(snaps)} snapshots")

        for i in range(len(snaps) - 1):
            stem_before, before = snaps[i]
            stem_after, after = snaps[i + 1]
            date_before = before["time_window"]["start"]
            date_after = after["time_window"]["start"]
            pair_label = f"{pid}-v{i+1}-to-v{i+2}"

            decomps = decompose_version_pair(before, after)
            summary = compute_pair_summary(decomps)

            pair_result = {
                "pair_id": pair_label,
                "position_id": pid,
                "position_name": pname,
                "from_date": date_before,
                "to_date": date_after,
                "from_version": i + 1,
                "to_version": i + 2,
                "from_sample_count": before.get("sample_stats", {}).get("included_samples", 0),
                "to_sample_count": after.get("sample_stats", {}).get("included_samples", 0),
                "summary": summary,
                "decompositions": [
                    {
                        "skill_id": d.skill_id,
                        "name_before": d.canonical_name_before,
                        "name_after": d.canonical_name_after,
                        "weight_before": d.weight_before,
                        "weight_after": d.weight_after,
                        "weight_delta": d.weight_delta,
                        "context_similarity": d.context_similarity,
                        "category_changed": d.category_changed,
                        "name_changed": d.name_changed,
                        "neighborhood_jaccard": d.neighborhood_jaccard,
                        "community_migrated": d.community_migrated,
                        "evidence_delta_normalized": d.evidence_delta_normalized,
                        "semantic_contribution": d.semantic_contribution,
                        "topological_contribution": d.topological_contribution,
                        "evidence_contribution": d.evidence_contribution,
                        "residual": d.residual,
                        "dominant_factor": d.dominant_factor,
                        "is_explained_by_artifact": d.is_explained_by_artifact,
                        "explanation": d.explanation,
                    }
                    for d in decomps
                ],
            }
            all_pair_results.append(pair_result)
            all_decomps_for_cross.append((pair_label, decomps))

            n = summary.get("total_skills_compared", 0)
            art = summary.get("artifact_driven_count", 0)
            mkt = summary.get("market_driven_count", 0)
            print(f"  {pair_label}: {n} skills, {art} artifact-driven, {mkt} market-driven, "
                  f"avg residual={summary.get('avg_residual', 'N/A')}")

    # Cross-pair analysis
    print(f"\nCross-pair analysis across {len(all_decomps_for_cross)} pairs...")
    cross = compute_cross_pair_analysis(all_decomps_for_cross)

    # Build final output
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "A20 semantic-topological decomposition",
        "pair_count": len(all_pair_results),
        "cross_pair_analysis": cross,
        "pair_results": all_pair_results,
    }

    # Save JSON
    json_path = OUTPUT_DIR / "a20_decomposition_results.json"
    json_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nJSON saved to: {json_path}")

    # Generate report
    generate_report(output, OUTPUT_DIR)
    print(f"Report saved to: {OUTPUT_DIR / 'a20_decomposition_report.md'}")


def generate_report(output: dict, output_dir: Path):
    lines: list[str] = []
    lines.append("# A20 语义/拓扑双分解报告")
    lines.append("")
    lines.append(f"生成时间：{output['generated_at']}  |  版本对：{output['pair_count']}")
    lines.append("")
    lines.append("## 1. 方法")
    lines.append("")
    lines.append("将技能在两个 GraphVersion 之间的权重变化分解为三路信号：")
    lines.append("")
    lines.append("| 信号 | 度量 | 说明 |")
    lines.append("|------|------|------|")
    lines.append("| **语义漂移** | context_similarity, category_changed, name_changed | 技能的类别或名称在两个版本间是否变化 |")
    lines.append("| **拓扑变化** | neighborhood_jaccard, community_migrated | 技能的同类别邻居重叠度和社区迁移 |")
    lines.append("| **证据变化** | evidence_delta_normalized | source/enterprise/sample 统计量的归一化变化 |")
    lines.append("| **残差** | residual | 以上三路信号不能解释的比例 → 真实市场变化 |")
    lines.append("")

    lines.append("## 2. 跨版本对追踪")
    lines.append("")
    cross = output.get("cross_pair_analysis", {})
    lines.append(f"- 追踪技能数：{cross.get('skills_tracked', 0)}")
    lines.append(f"- 主导因子始终一致：{cross.get('consistent_dominant_factor', 0)}")
    lines.append(f"- 主导因子跨版本变化：{cross.get('variable_dominant_factor', 0)}")
    lines.append(f"- 社区迁移技能数：{len(cross.get('migratory_skills', []))}")
    lines.append("")

    # Migratory skills
    migratory = cross.get("migratory_skills", [])
    if migratory:
        lines.append("### 社区迁移技能")
        lines.append("")
        lines.append("| 技能 | 迁移次数 | 各版本对中的主导因子 |")
        lines.append("|------|----------|---------------------|")
        for m in migratory[:15]:
            dominants = " → ".join(
                t["dominant_factor"] for t in m["trace"]
            )
            lines.append(f"| {m['name']} | {m['migration_count']} | {dominants} |")
        lines.append("")

    # Per-position summary
    lines.append("## 3. 各岗位分解汇总")
    lines.append("")
    by_position: dict[str, list[dict]] = {}
    for pr in output["pair_results"]:
        pid = pr["position_name"]
        if pid not in by_position:
            by_position[pid] = []
        by_position[pid].append(pr)

    for pname, pairs in by_position.items():
        lines.append(f"### {pname}")
        lines.append("")
        lines.append("| Pair | 技能数 | 伪影驱动 | 市场驱动 | 迁移 | 平均残差 | 平均证据Δ |")
        lines.append("|------|--------|----------|----------|------|----------|-----------|")
        for pr in pairs:
            s = pr["summary"]
            lines.append(
                f"| {pr['pair_id']} | {s.get('total_skills_compared', 0)} "
                f"| {s.get('artifact_driven_count', 0)} "
                f"| {s.get('market_driven_count', 0)} "
                f"| {s.get('community_migration_count', 0)} "
                f"| {s.get('avg_residual', 0):.3f} "
                f"| {s.get('avg_evidence_contribution', 0):.3f} |"
            )
        lines.append("")

    # Top changes by residual (market signal)
    lines.append("## 4. Top 20 真实市场信号（残差 > 0.8）")
    lines.append("")
    all_decomps: list[dict] = []
    for pr in output["pair_results"]:
        for d in pr["decompositions"]:
            d["_pair"] = pr["pair_id"]
            d["_position"] = pr["position_name"]
            all_decomps.append(d)

    high_residual = sorted(
        [d for d in all_decomps if d["residual"] > 0.8],
        key=lambda x: -abs(x["weight_delta"]),
    )[:20]

    if high_residual:
        lines.append("| 技能 | 岗位 | 版本对 | Δ权重 | 残差 | 说明 |")
        lines.append("|------|------|--------|-------|------|------|")
        for d in high_residual:
            lines.append(
                f"| {d['name_after']} | {d['_position']} | {d['_pair']} "
                f"| {d['weight_delta']:+.3f} | {d['residual']:.3f} "
                f"| {d['explanation'][:60]} |"
            )
    else:
        lines.append("无高残差技能。")
    lines.append("")

    # Top artifact-driven changes
    lines.append("## 5. Top 20 伪影驱动变化（非市场信号）")
    lines.append("")
    artifact_driven = sorted(
        [d for d in all_decomps if d["is_explained_by_artifact"]],
        key=lambda x: -abs(x["weight_delta"]),
    )[:20]

    if artifact_driven:
        lines.append("| 技能 | 岗位 | 版本对 | Δ权重 | 主导因子 | 说明 |")
        lines.append("|------|------|--------|-------|----------|------|")
        for d in artifact_driven:
            lines.append(
                f"| {d['name_after']} | {d['_position']} | {d['_pair']} "
                f"| {d['weight_delta']:+.3f} | {d['dominant_factor']} "
                f"| {d['explanation'][:60]} |"
            )
    else:
        lines.append("无伪影驱动变化。")
    lines.append("")

    # Community migration events
    lines.append("## 6. 社区迁移事件详解")
    lines.append("")
    migrations = [d for d in all_decomps if d["community_migrated"]]
    if migrations:
        for d in sorted(migrations, key=lambda x: -abs(x["weight_delta"])):
            lines.append(f"- **{d['name_before']} → {d['name_after']}** ")
            lines.append(f"  ({d['_pair']}, Δw={d['weight_delta']:+.3f}, "
                        f"residual={d['residual']:.3f}): {d['explanation']}")
        lines.append("")
    else:
        lines.append("无社区迁移事件。")
        lines.append("")

    report_path = output_dir / "a20_decomposition_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    run()
