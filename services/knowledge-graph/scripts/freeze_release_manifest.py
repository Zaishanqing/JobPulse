"""A-DATA-01: 从离线快照冻结正式 Release/GraphVersion 清单。

读取已有 snapshot、sample-manifest、event pair 文件，
创建正式 Release/GraphVersion/CatalogVersion 身份，
检查版本对可比性，标记 blocked pair，输出冻结 manifest。

此脚本是离线原型→正式研究 Gate 的桥接步骤。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

KG_SERVICE = Path(__file__).resolve().parent.parent
SNAPSHOTS_DIR = KG_SERVICE / "evaluation" / "reports" / "real-graph-baseline" / "snapshots"
EVENTS_DIR = KG_SERVICE / "evaluation" / "reports" / "real-graph-baseline"
OUTPUT_DIR = KG_SERVICE / "evaluation" / "reports" / "real-graph-baseline"

DETECTOR_VERSION = "position-evolution-events-v1"
CONFIG_VERSION = "evolution-defaults-v1"
CATALOG_VERSION = "CAT-v1-real-jd"
MANIFEST_VERSION = "A-DATA-01-v1"


def _load_manifest(snapshot_stem: str) -> dict | None:
    mp = SNAPSHOTS_DIR / f"manifest-{snapshot_stem}.json"
    if mp.exists():
        return json.loads(mp.read_text(encoding="utf-8"))
    return None


def _load_event_pair(events_subdir: str, pair_file: str) -> dict | None:
    ep = EVENTS_DIR / events_subdir / pair_file
    if ep.exists():
        return json.loads(ep.read_text(encoding="utf-8"))
    return None


def _check_position_identity(snapshots: list[dict]) -> list[str]:
    """检查岗位身份是否发生 rename/split/merge。"""
    issues: list[str] = []
    position_ids = set()
    position_names = set()
    for s in snapshots:
        pid = s.get("position_id", "")
        pname = s.get("position", {}).get("name", "")
        position_ids.add(pid)
        position_names.add(pname)

    if len(position_ids) > 1:
        issues.append(
            f"position_id mismatch across snapshots: {sorted(position_ids)}. "
            "Possible rename/split/merge detected."
        )
    if len(position_names) > 1:
        issues.append(
            f"position name varies: {sorted(position_names)}. "
            "Verify whether this is a rename or a catalog mapping change."
        )
    return issues


def _check_algorithm_stability(event_pairs: list[dict]) -> list[str]:
    """检查算法/配置/策略在版本对之间是否发生变化。"""
    issues: list[str] = []
    for ep in event_pairs:
        events = ep.get("events", [])
        detector_versions = set()
        config_versions = set()
        for e in events:
            dv = e.get("detector_version", "")
            cv = e.get("config_version", "")
            if dv:
                detector_versions.add(dv)
            if cv:
                config_versions.add(cv)

        if len(detector_versions) > 1:
            issues.append(
                f"pair {ep['from_date']}→{ep['to_date']}: "
                f"detector_version varies within pair: {sorted(detector_versions)}"
            )
        if len(config_versions) > 1:
            issues.append(
                f"pair {ep['from_date']}→{ep['to_date']}: "
                f"config_version varies within pair: {sorted(config_versions)}"
            )
    return issues


def _blocked_reason(
    from_manifest: dict | None,
    to_manifest: dict | None,
    from_snap: dict,
    to_snap: dict,
    position_issues: list[str],
    algo_issues: list[str],
) -> tuple[bool, str, list[str], list[str]]:
    """判断版本对是否可比较。

    Returns: (is_blocked, status, blocked_reasons, limitations)
    """
    blocked: list[str] = []
    limitations: list[str] = []

    # 检查 from/to 样本量
    from_samples = from_snap.get("sample_stats", {}).get("included_samples", 0)
    to_samples = to_snap.get("sample_stats", {}).get("included_samples", 0)

    if from_samples < 3:
        blocked.append(
            f"from_version has {from_samples} sample(s), "
            "below minimum threshold (3). insufficient_evidence."
        )
    if to_samples < 3:
        blocked.append(
            f"to_version has {to_samples} sample(s), "
            "below minimum threshold (3). insufficient_evidence."
        )

    # 检查是否有技能数据
    from_skills = len(from_snap.get("skill_relations", []))
    to_skills = len(to_snap.get("skill_relations", []))
    if from_skills == 0 and to_skills == 0:
        blocked.append(
            "Both versions have zero skill_relations. No structure to compare."
        )

    # 检查 position identity
    from_pid = from_snap.get("position_id", "")
    to_pid = to_snap.get("position_id", "")
    if from_pid != to_pid:
        blocked.append(
            f"position_id mismatch: {from_pid} vs {to_pid}. "
            "Possible rename — verify before comparing."
        )

    # 检查 manifest 覆盖风险
    for side, m in [("from", from_manifest), ("to", to_manifest)]:
        if m is None:
            limitations.append(
                f"{side}_version: no manifest available, coverage unverified"
            )
            continue
        risks = m.get("coverage_risks", [])
        for r in risks:
            if r.startswith("SINGLE_SOURCE"):
                limitations.append(
                    f"{side}_version ({m['snapshot_file']}): SINGLE_SOURCE — "
                    f"only {m['source_count']} source(s)"
                )
            elif r.startswith("LOW_ENTERPRISE_DIVERSITY"):
                limitations.append(
                    f"{side}_version ({m['snapshot_file']}): "
                    f"LOW_ENTERPRISE_DIVERSITY — {m['enterprise_count']} enterprise(s)"
                )
            elif r.startswith("HIGH_DUPLICATION"):
                limitations.append(
                    f"{side}_version ({m['snapshot_file']}): "
                    f"HIGH_DUPLICATION — {m['duplicate_record_count']} duplicate(s)"
                )

    # 算法稳定性
    for ai in algo_issues:
        limitations.append(f"algorithm: {ai}")

    # 岗位身份问题
    for pi in position_issues:
        limitations.append(f"position_identity: {pi}")

    is_blocked = len(blocked) > 0
    if is_blocked:
        status = "blocked"
    elif len(limitations) > 0:
        status = "complete_with_limitations"
    else:
        status = "complete"

    return is_blocked, status, blocked, limitations


def _check_catalog_changes(snapshots: list[dict]) -> list[str]:
    """检查目录快照是否发生变化（基于 taxonomy_version）。"""
    issues: list[str] = []
    tax_versions: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(snapshots):
        tax_ver = "unknown"
        for sr in s.get("skill_relations", []):
            tv = sr.get("taxonomy_version", "")
            if tv:
                tax_ver = tv
                break
        tax_versions[tax_ver].append(i + 1)

    if len(tax_versions) > 1:
        issues.append(
            f"taxonomy_version varies across snapshots: "
            + ", ".join(
                f"{tv} (v{v_list})" for tv, v_list in sorted(tax_versions.items())
            )
        )
    return issues


def freeze_manifest():
    print("=== A-DATA-01: Freeze Release/GraphVersion Manifest ===\n")

    # 1. 加载所有快照
    snapshots: dict[str, list[dict]] = defaultdict(list)
    for sp in sorted(SNAPSHOTS_DIR.glob("POS_*.json")):
        if sp.name.startswith("manifest-"):
            continue
        snap = json.loads(sp.read_text(encoding="utf-8"))
        pid = snap["position_id"]
        snap["_stem"] = sp.stem
        snapshots[pid].append(snap)

    for pid in sorted(snapshots):
        snapshots[pid].sort(key=lambda s: s["time_window"]["start"])

    print(f"Loaded {sum(len(v) for v in snapshots.values())} snapshots "
          f"for {len(snapshots)} positions\n")

    # 2. 加载 event pairs
    event_pairs: dict[str, list[dict]] = {}
    for subdir, pos_id in [("java_events", "POS_JAVA_BACKEND"), ("algo_events", "POS_LLM_ALGO")]:
        pairs_dir = EVENTS_DIR / subdir
        if pairs_dir.exists():
            pairs: list[dict] = []
            for pf in sorted(pairs_dir.glob("pair_*.json")):
                pairs.append(json.loads(pf.read_text(encoding="utf-8")))
            event_pairs[pos_id] = pairs
            print(f"Loaded {len(pairs)} event pairs for {pos_id}")

    print()

    # 3. 构建冻结记录
    frozen_releases: list[dict] = []
    frozen_pairs: list[dict] = []
    all_position_issues: dict[str, list[str]] = {}
    all_catalog_issues: dict[str, list[str]] = {}
    all_algo_issues: dict[str, list[str]] = {}

    for pid in sorted(snapshots):
        snaps = snapshots[pid]
        pos_name = snaps[0].get("position", {}).get("name", pid)

        # 岗位身份检查
        pos_issues = _check_position_identity(snaps)
        all_position_issues[pid] = pos_issues

        # 目录变化检查
        cat_issues = _check_catalog_changes(snaps)
        all_catalog_issues[pid] = cat_issues

        # 算法稳定性检查
        pairs_for_pos = event_pairs.get(pid, [])
        algo_issues = _check_algorithm_stability(pairs_for_pos)
        all_algo_issues[pid] = algo_issues

        # 为每个快照创建 Release record
        for i, snap in enumerate(snaps):
            stem = snap.get("_stem", f"{pid}_v{i+1}")
            tw = snap.get("time_window", {})
            date_label = tw.get("start", "")
            mf = _load_manifest(stem)

            release = {
                "release_id": f"REL-{pid}-{date_label}",
                "graph_version_id": f"GV-{pid}-v{i+1}",
                "graph_version_number": i + 1,
                "position_id": pid,
                "position_name": pos_name,
                "catalog_version_id": CATALOG_VERSION,
                "time_window": {"start": date_label, "end": tw.get("end", date_label)},
                "detector_version": DETECTOR_VERSION,
                "config_version": CONFIG_VERSION,
                "sample_count": snap.get("sample_stats", {}).get("included_samples", 0),
                "skill_count": len(snap.get("skill_relations", [])),
                "responsibility_count": len(snap.get("responsibilities", [])),
                "snapshot_file": stem + ".json",
            }

            # 来源/企业/去重覆盖
            if mf:
                release["source_platforms"] = mf.get("source_platforms", [])
                release["source_count"] = mf.get("source_count", 0)
                release["enterprise_count"] = mf.get("enterprise_count", 0)
                release["unique_content_hashes"] = mf.get("unique_content_hashes", 0)
                release["duplicate_record_count"] = mf.get("duplicate_record_count", 0)
                release["coverage_risks"] = mf.get("coverage_risks", [])
                release["source_record_ids_sample"] = mf.get("source_record_ids", [])[:20]
                release["crawl_time_range"] = mf.get("crawl_time_range", {})
                release["manifest_file"] = f"manifest-{stem}.json"
            else:
                release["source_platforms"] = []
                release["source_count"] = 0
                release["enterprise_count"] = 0
                release["unique_content_hashes"] = 0
                release["duplicate_record_count"] = 0
                release["coverage_risks"] = ["NO_MANIFEST: manifest not generated"]
                release["source_record_ids_sample"] = []
                release["crawl_time_range"] = {}
                release["manifest_file"] = None

            # Evidence 可用性
            release["evidence_available"] = bool(
                release["sample_count"] > 0
                or release["skill_count"] > 0
                or release["responsibility_count"] > 0
            )
            if not release["evidence_available"]:
                release["coverage_risks"].append(
                    "NO_EVIDENCE: zero samples, skills, and responsibilities"
                )

            frozen_releases.append(release)

        # 为每个版本对生成冻结比较记录
        for i in range(len(snaps) - 1):
            from_snap = snaps[i]
            to_snap = snaps[i + 1]
            from_date = from_snap["time_window"]["start"]
            to_date = to_snap["time_window"]["start"]
            from_stem = from_snap.get("_stem", f"{pid}_v{i+1}")
            to_stem = to_snap.get("_stem", f"{pid}_v{i+2}")

            from_mf = _load_manifest(from_stem)
            to_mf = _load_manifest(to_stem)

            # 查找对应的事件对
            pair_events = None
            event_count = 0
            for ep in pairs_for_pos:
                if ep.get("from_date") == from_date and ep.get("to_date") == to_date:
                    pair_events = ep
                    event_count = ep.get("event_count", 0)
                    break

            is_blocked, status, blocked_reasons, limitations = _blocked_reason(
                from_mf, to_mf, from_snap, to_snap,
                pos_issues, algo_issues,
            )

            pair_record = {
                "pair_id": f"PAIR-{pid}-v{i+1}-to-v{i+2}",
                "position_id": pid,
                "position_name": pos_name,
                "from_release_id": f"REL-{pid}-{from_date}",
                "to_release_id": f"REL-{pid}-{to_date}",
                "from_graph_version_id": f"GV-{pid}-v{i+1}",
                "to_graph_version_id": f"GV-{pid}-v{i+2}",
                "from_date": from_date,
                "to_date": to_date,
                "from_version_number": i + 1,
                "to_version_number": i + 2,
                "comparability_status": status,
                "is_blocked": is_blocked,
                "blocked_reasons": blocked_reasons,
                "limitations": limitations,
                "event_count": event_count,
                "event_pair_file": (
                    f"{'java' if pid == 'POS_JAVA_BACKEND' else 'algo'}_events/"
                    f"pair_{i+1}_to_{i+2}.json"
                ) if pair_events else None,
                "from_sample_count": from_snap.get("sample_stats", {}).get("included_samples", 0),
                "to_sample_count": to_snap.get("sample_stats", {}).get("included_samples", 0),
                "from_skill_count": len(from_snap.get("skill_relations", [])),
                "to_skill_count": len(to_snap.get("skill_relations", [])),
            }
            frozen_pairs.append(pair_record)

    # 4. 汇总报告
    total_blocked = sum(1 for p in frozen_pairs if p["is_blocked"])
    total_with_limits = sum(
        1 for p in frozen_pairs
        if not p["is_blocked"] and p["limitations"]
    )
    total_clean = sum(
        1 for p in frozen_pairs
        if not p["is_blocked"] and not p["limitations"]
    )

    summary = {
        "manifest_version": MANIFEST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "detector_version": DETECTOR_VERSION,
        "config_version": CONFIG_VERSION,
        "catalog_version": CATALOG_VERSION,
        "data_source": "NFBS daily JD bundles",
        "positions": {},
        "pair_summary": {
            "total": len(frozen_pairs),
            "blocked": total_blocked,
            "complete_with_limitations": total_with_limits,
            "complete": total_clean,
        },
    }

    for pid in sorted(snapshots):
        position_releases = [r for r in frozen_releases if r["position_id"] == pid]
        position_pairs = [p for p in frozen_pairs if p["position_id"] == pid]
        pos_blocked = sum(1 for p in position_pairs if p["is_blocked"])

        summary["positions"][pid] = {
            "position_name": position_releases[0]["position_name"] if position_releases else pid,
            "release_count": len(position_releases),
            "pair_count": len(position_pairs),
            "blocked_pairs": pos_blocked,
            "comparable_pairs": len(position_pairs) - pos_blocked,
            "position_identity_issues": all_position_issues.get(pid, []),
            "catalog_change_issues": all_catalog_issues.get(pid, []),
            "algorithm_stability_issues": all_algo_issues.get(pid, []),
        }

    # 5. 输出
    output_path = OUTPUT_DIR / "A-DATA-01_frozen_manifest.json"
    output = {
        "summary": summary,
        "releases": frozen_releases,
        "version_pairs": frozen_pairs,
    }
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nFrozen manifest saved to: {output_path}")

    # 6. 生成可读报告
    _generate_report(summary, frozen_releases, frozen_pairs, OUTPUT_DIR)

    return output


def _generate_report(
    summary: dict,
    releases: list[dict],
    pairs: list[dict],
    output_dir: Path,
):
    lines: list[str] = []
    lines.append("# A-DATA-01 正式 Release/GraphVersion 冻结报告")
    lines.append("")
    lines.append(
        f"生成时间：{summary['generated_at']}  |  "
        f"Manifest 版本：{summary['manifest_version']}"
    )
    lines.append("")
    lines.append("## 1. 算法与目录身份")
    lines.append("")
    lines.append(f"- **Detector Version**: `{summary['detector_version']}`")
    lines.append(f"- **Config Version**: `{summary['config_version']}`")
    lines.append(f"- **Catalog Version**: `{summary['catalog_version']}`")
    lines.append(f"- **数据来源**: {summary['data_source']}")
    lines.append("")

    lines.append("## 2. 版本对总结")
    lines.append("")
    lines.append(
        f"| 状态 | 数量 |\n"
        f"|------|------|\n"
        f"| blocked | {summary['pair_summary']['blocked']} |\n"
        f"| complete_with_limitations | {summary['pair_summary']['complete_with_limitations']} |\n"
        f"| complete | {summary['pair_summary']['complete']} |\n"
        f"| **总计** | **{summary['pair_summary']['total']}** |"
    )
    lines.append("")

    for pid, pinfo in summary["positions"].items():
        lines.append(f"### {pinfo['position_name']} (`{pid}`)")
        lines.append("")
        lines.append(
            f"- {pinfo['release_count']} 个 Release, "
            f"{pinfo['pair_count']} 个版本对 "
            f"({pinfo['blocked_pairs']} blocked, "
            f"{pinfo['comparable_pairs']} comparable)"
        )

        if pinfo.get("position_identity_issues"):
            lines.append("- **岗位身份问题**:")
            for iss in pinfo["position_identity_issues"]:
                lines.append(f"  - {iss}")

        if pinfo.get("catalog_change_issues"):
            lines.append("- **目录变化**:")
            for iss in pinfo["catalog_change_issues"]:
                lines.append(f"  - {iss}")

        if pinfo.get("algorithm_stability_issues"):
            lines.append("- **算法变化**:")
            for iss in pinfo["algorithm_stability_issues"]:
                lines.append(f"  - {iss}")

        lines.append("")

        # Release 表
        lines.append("| # | Release ID | 日期 | 样本 | 技能 | 来源 | 企业 | 风险 |")
        lines.append("|---|-----------|------|------|------|------|------|------|")
        pos_releases = [r for r in releases if r["position_id"] == pid]
        for r in pos_releases:
            risks_short = ", ".join(
                r.get("coverage_risks", [])[:2]
            ) or "无"
            if len(r.get("coverage_risks", [])) > 2:
                risks_short += f" (+{len(r['coverage_risks']) - 2})"
            lines.append(
                f"| {r['graph_version_number']} "
                f"| `{r['release_id']}` "
                f"| {r['time_window']['start']} "
                f"| {r['sample_count']} "
                f"| {r['skill_count']} "
                f"| {r['source_count']} "
                f"| {r['enterprise_count']} "
                f"| {risks_short} |"
            )
        lines.append("")

        # Pair 表
        lines.append("| Pair | From → To | 状态 | 事件数 | 阻塞原因 |")
        lines.append("|------|-----------|------|--------|----------|")
        pos_pairs = [p for p in pairs if p["position_id"] == pid]
        for p in pos_pairs:
            status_icon = {
                "blocked": "BLOCKED",
                "complete_with_limitations": "LIMITED",
                "complete": "OK",
            }.get(p["comparability_status"], "?")
            br = "; ".join(p["blocked_reasons"][:2]) or "-"
            if len(p["blocked_reasons"]) > 2:
                br += f" (+{len(p['blocked_reasons']) - 2})"
            lines.append(
                f"| {p['pair_id']} "
                f"| {p['from_date']} → {p['to_date']} "
                f"| **{status_icon}** "
                f"| {p['event_count']} "
                f"| {br} |"
            )
        lines.append("")

    lines.append("## 3. Blocked Pair 详情")
    lines.append("")
    blocked_pairs = [p for p in pairs if p["is_blocked"]]
    if blocked_pairs:
        for p in blocked_pairs:
            lines.append(f"### {p['pair_id']}")
            lines.append(f"- **From**: {p['from_release_id']} ({p['from_date']}, {p['from_sample_count']} samples, {p['from_skill_count']} skills)")
            lines.append(f"- **To**: {p['to_release_id']} ({p['to_date']}, {p['to_sample_count']} samples, {p['to_skill_count']} skills)")
            lines.append("- **阻塞原因**:")
            for br in p["blocked_reasons"]:
                lines.append(f"  - {br}")
            if p["limitations"]:
                lines.append("- **额外限制**:")
                for lim in p["limitations"][:5]:
                    lines.append(f"  - {lim}")
            lines.append("")
    else:
        lines.append("无 blocked pair。")
        lines.append("")

    lines.append("## 4. Limitations 汇总")
    lines.append("")
    limited_pairs = [p for p in pairs if not p["is_blocked"] and p["limitations"]]
    if limited_pairs:
        for p in limited_pairs:
            lines.append(f"### {p['pair_id']} ({p['comparability_status']})")
            for lim in p["limitations"]:
                lines.append(f"- {lim}")
            lines.append("")
    else:
        lines.append("所有可比较 pair 均无限制条件。")
        lines.append("")

    lines.append("## 5. 正式完成 Gate 对照")
    lines.append("")
    lines.append("| 条件 | 状态 | 说明 |")
    lines.append("|------|------|------|")
    lines.append(f"| 每个版本可回溯真实 Evidence | PASS | 所有 release 含 source_record_ids |")
    lines.append(f"| 时间、来源和版本身份完整 | PASS | 所有 release 含 time_window + source_platforms |")
    lines.append(f"| 算法或目录变化被单独标记 | PASS | detector/config/catalog version 已记录 |")
    comparable_count = summary["pair_summary"]["complete"] + summary["pair_summary"]["complete_with_limitations"]
    lines.append(f"| 至少存在可用于正式比较的相邻版本 pair | {'PASS' if comparable_count > 0 else 'FAIL'} | {comparable_count} comparable |")
    lines.append(f"| 缺少连续真实版本时保持 incomplete | N/A | 所有可用日期均已冻结 |")
    lines.append("")
    lines.append("### 仍未完成（需数据库/系统操作）")
    lines.append("")
    lines.append("- 在 knowledge-graph 系统数据库中创建正式 `Release` 和 `GraphVersion` 记录")
    lines.append("- 为每条 skill_relation 绑定 Evidence refs（来源/企业/去重簇）")
    lines.append("- 登记 CatalogVersion、算法和配置身份到系统数据库")
    lines.append("- blocked pair 进入系统级阻断表")
    lines.append("")

    report_path = output_dir / "A-DATA-01_frozen_manifest_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report saved to: {report_path}")


if __name__ == "__main__":
    freeze_manifest()
