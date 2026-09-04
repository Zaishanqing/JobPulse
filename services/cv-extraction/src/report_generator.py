from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .capability_verification import (
    CAPABILITY_VERIFICATION_DERIVATION_VERSION,
)
from .validator import BUSINESS_VALIDATOR_VERSION


REPORT_GENERATOR_VERSION = "2.3"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_run_dir(output_dir: Path) -> Path:
    runs = [path for path in (output_dir / "runs").iterdir() if path.is_dir()]
    if not runs:
        raise FileNotFoundError("No run directories found")
    return max(runs, key=lambda path: path.stat().st_mtime)


def summarize_run(run_dir: Path) -> dict[str, Any]:
    final = run_dir / "final"
    manifest = read_json(run_dir / "manifest.json")
    annotations = read_jsonl(final / "annotations.jsonl")
    normalized = read_jsonl(final / "normalized_annotations.jsonl")
    match_features = read_jsonl(final / "match_features.jsonl")
    capability_profiles = read_jsonl(final / "capability_profiles.jsonl")
    capability_evidence_links = read_jsonl(final / "capability_evidence_links.jsonl")
    flags = read_jsonl(final / "review_flags.jsonl")
    failures = read_jsonl(final / "failed_cases.jsonl")
    illegal = read_jsonl(final / "illegal_enum_cases.jsonl")
    logs = read_jsonl(run_dir / "logs.jsonl")
    api_attempts = [event for event in logs if event.get("event_type") == "api_attempt_finished"]
    initial_api_attempts = [event for event in api_attempts if event.get("mode") == "initial"]
    retry_api_attempts = [event for event in api_attempts if event.get("mode") != "initial"]
    transport_retry_count = sum(
        max(0, int(event.get("transport_attempt_count", 0)) - 1)
        for event in api_attempts
    )

    def average_elapsed_ms(events: list[dict[str, Any]]) -> float:
        if not events:
            return 0.0
        return round(sum(float(event.get("elapsed_ms", 0)) for event in events) / len(events), 1)

    counts = Counter()
    item_types: Counter[str] = Counter()
    proficiency_levels: Counter[str] = Counter()
    annotation_ids: list[str | None] = []
    item_ids_by_document: dict[str, set[str]] = {}
    for record in annotations:
        document_id = record.get("document_id")
        annotation_ids.append(document_id)
        counts["education"] += len(record.get("education", []))
        counts["work"] += len(record.get("work_experience", []))
        counts["projects"] += len(record.get("project_experience", []))
        counts["languages"] += len(record.get("languages", []))
        counts["certificates"] += len(record.get("certificates", []))
        counts["awards"] += len(record.get("awards", []))
        counts["self_evaluations"] += len(record.get("self_evaluation", []))
        skills = list(record.get("skills", []))
        for work in record.get("work_experience", []):
            counts["responsibilities"] += len(work.get("responsibilities", []))
            counts["achievements"] += len(work.get("achievements", []))
            work_skills = work.get("tech_stack", [])
            skills.extend(work_skills)
            counts["work_skills"] += len(work_skills)
        for project in record.get("project_experience", []):
            project_skills = project.get("tech_stack", [])
            skills.extend(project_skills)
            counts["project_skills"] += len(project_skills)
            counts["project_highlights"] += len(project.get("highlights", []))
        counts["declared_skills"] += len(record.get("skills", []))
        counts["all_skills"] += len(skills)
        if document_id:
            item_ids_by_document[document_id] = {item.get("item_id") for item in skills if item.get("item_id")}
        for item in skills:
            item_types[item.get("item_type", "other")] += 1
            proficiency_levels[item.get("proficiency") or "missing"] += 1

    normalized_ids = [record.get("document_id") for record in normalized]
    failed_ids = [record.get("cv_id") for record in failures]
    unresolved_items = Counter(
        item
        for record in normalized
        for item in record.get("unresolved_items", [])
    )
    normalized_skill_count = sum(len(record.get("normalized_skills", [])) for record in normalized)
    resolved_skill_count = sum(
        skill.get("identity_resolution_status") == "resolved"
        for record in normalized
        for skill in record.get("normalized_skills", [])
    )
    annotation_id_set = {value for value in annotation_ids if value}
    normalized_id_set = {value for value in normalized_ids if value}
    failed_id_set = {value for value in failed_ids if value}
    flagged_document_count = len({flag.get("cv_id") for flag in flags if flag.get("cv_id")})
    match_feature_ids = [feature.get("feature_id") for feature in match_features]
    match_feature_document_ids = {
        feature.get("document_id") for feature in match_features if feature.get("document_id")
    }
    resolved_match_feature_count = sum(
        feature.get("resolution_status") == "resolved" for feature in match_features
    )
    position_classifications = Counter(
        feature.get("structured_values", {}).get(
            "classification_status", "missing"
        )
        for feature in match_features
        if feature.get("feature_type") == "role"
    )
    capability_profile_ids = [profile.get("profile_id") for profile in capability_profiles]
    capability_link_ids = [link.get("link_id") for link in capability_evidence_links]

    def flag_reference_exists(flag: dict[str, Any]) -> bool:
        document_id = flag.get("cv_id")
        if document_id not in annotation_id_set:
            return False
        if flag.get("rule_scope") == "skill":
            return flag.get("item_id") in item_ids_by_document.get(document_id, set())
        return True

    integrity_checks = {
        "annotations_match_manifest_success": len(annotations) == manifest.get("success_count", 0),
        "normalized_match_annotations": len(normalized) == len(annotations),
        "failures_match_manifest_failed": len(failures) == manifest.get("failed_count", 0),
        "review_flags_match_manifest": len(flags) == manifest.get("review_flag_count", 0),
        "annotation_document_ids_unique": len(annotation_ids) == len(annotation_id_set),
        "normalized_document_ids_match_annotations": normalized_id_set == annotation_id_set,
        "success_and_failure_document_ids_disjoint": annotation_id_set.isdisjoint(failed_id_set),
        "review_flags_reference_existing_objects": all(flag_reference_exists(flag) for flag in flags),
        "match_feature_count_matches_manifest": len(match_features)
        == manifest.get("match_feature_count", 0),
        "match_feature_ids_unique": len(match_feature_ids) == len(set(match_feature_ids)),
        "match_feature_documents_match_annotations": match_feature_document_ids
        == annotation_id_set,
        "capability_profile_count_matches_manifest": len(capability_profiles)
        == manifest.get("capability_profile_count", 0),
        "capability_evidence_link_count_matches_manifest": len(capability_evidence_links)
        == manifest.get("capability_evidence_link_count", 0),
        "capability_profile_ids_unique": len(capability_profile_ids)
        == len(set(capability_profile_ids)),
        "capability_evidence_link_ids_unique": len(capability_link_ids)
        == len(set(capability_link_ids)),
        "capability_links_reference_existing_profiles": all(
            set(profile.get("evidence_link_ids", [])).issubset(set(capability_link_ids))
            for profile in capability_profiles
        ),
        "business_validator_version_matches": manifest.get("business_validator_version")
        == BUSINESS_VALIDATOR_VERSION,
        "capability_verification_derivation_version_matches": manifest.get(
            "capability_verification_derivation_version"
        )
        == CAPABILITY_VERIFICATION_DERIVATION_VERSION,
        "report_generator_version_matches": manifest.get("report_generator_version")
        == REPORT_GENERATOR_VERSION,
        "api_attempt_events_match_manifest": len(api_attempts) == manifest.get("api_call_count", 0),
        "transport_retries_match_manifest": transport_retry_count
        == manifest.get("transport_retry_count", 0),
    }
    return {
        "manifest": manifest,
        "counts": {
            **counts,
            "flagged_documents": flagged_document_count,
            "normalized_skills": normalized_skill_count,
            "resolved_skills": resolved_skill_count,
            "unresolved_items": sum(unresolved_items.values()),
            "match_features": len(match_features),
            "resolved_match_features": resolved_match_feature_count,
            "capability_profiles": len(capability_profiles),
            "capability_evidence_links": len(capability_evidence_links),
        },
        "match_feature_types": Counter(
            feature.get("feature_type", "unknown") for feature in match_features
        ).most_common(),
        "position_classifications": position_classifications.most_common(),
        "item_types": item_types.most_common(),
        "proficiency_levels": proficiency_levels.most_common(),
        "review_flags": Counter(flag.get("issue_type") for flag in flags).most_common(),
        "unresolved_items": unresolved_items.most_common(30),
        "failures": Counter(case.get("error_type") for case in failures).most_common(),
        "failed_cases": failures,
        "illegal_enums": illegal,
        "api_timing": {
            "attempt_count": len(api_attempts),
            "initial_average_ms": average_elapsed_ms(initial_api_attempts),
            "initial_max_ms": max((float(event.get("elapsed_ms", 0)) for event in initial_api_attempts), default=0.0),
            "retry_average_ms": average_elapsed_ms(retry_api_attempts),
            "retry_max_ms": max((float(event.get("elapsed_ms", 0)) for event in retry_api_attempts), default=0.0),
            "transport_retry_count": transport_retry_count,
        },
        "integrity_checks": integrity_checks,
    }


def generate_report(summary: dict[str, Any]) -> str:
    manifest = summary["manifest"]
    counts = summary["counts"]
    total = manifest.get("total_rows", 0)
    success = manifest.get("success_count", 0)
    failed = manifest.get("failed_count", 0)
    rate = f"{success / total:.1%}" if total else "不适用"
    failure_rate = f"{failed / total:.1%}" if total else "不适用"
    flagged_rate = f"{counts.get('flagged_documents', 0) / success:.1%}" if success else "不适用"
    normalized_skills = counts.get("normalized_skills", 0)
    resolved_skills = counts.get("resolved_skills", 0)
    normalization_rate = f"{resolved_skills / normalized_skills:.1%}" if normalized_skills else "不适用"
    timing = summary["api_timing"]
    lines = [
        "# 简历抽取运行研究报告",
        "",
        "## 总览",
        "",
        f"本轮处理 {total} 份简历，成功 {success} 份，失败 {failed} 份，流水线通过率 {rate}。",
        "流水线通过率不代表抽取准确率或召回率。",
        "",
        "| 指标 | 数量 |",
        "|---|---:|",
        f"| 教育经历 | {counts.get('education', 0)} |",
        f"| 工作经历 | {counts.get('work', 0)} |",
        f"| 工作职责原子事实 | {counts.get('responsibilities', 0)} |",
        f"| 工作成果原子事实 | {counts.get('achievements', 0)} |",
        f"| 工作技术项 | {counts.get('work_skills', 0)} |",
        f"| 项目经历 | {counts.get('projects', 0)} |",
        f"| 项目技术项 | {counts.get('project_skills', 0)} |",
        f"| 项目亮点原子事实 | {counts.get('project_highlights', 0)} |",
        f"| 声明技能 | {counts.get('declared_skills', 0)} |",
        f"| 未归一化技能项 | {counts.get('unresolved_items', 0)} |",
        f"| 技能归一化覆盖率 | {resolved_skills}/{normalized_skills} ({normalization_rate}) |",
        f"| MatchFeature | {counts.get('match_features', 0)} |",
        f"| 可直接匹配的 MatchFeature | {counts.get('resolved_match_features', 0)} |",
        f"| 能力验证档案 | {counts.get('capability_profiles', 0)} |",
        f"| 经历—能力证据链 | {counts.get('capability_evidence_links', 0)} |",
        f"| 有审查标记的简历 | {counts.get('flagged_documents', 0)} |",
        f"| 审查标记简历率 | {flagged_rate} |",
        f"| 硬失败率 | {failure_rate} |",
        f"| 校验重试 | {manifest.get('validation_retry_count', 0)} |",
        f"| 局部修复调用 | {manifest.get('local_repair_count', 0)} |",
        f"| 全量重抽取调用 | {manifest.get('full_reextract_count', 0)} |",
        f"| 校验重试恢复 | {manifest.get('validation_retry_recovered_count', 0)} |",
        f"| 初次通过 | {manifest.get('initial_pass_count', 0)} |",
        f"| 第一次重试恢复 | {manifest.get('recovered_after_first_retry_count', 0)} |",
        f"| 第二次重试恢复 | {manifest.get('recovered_after_second_retry_count', 0)} |",
        f"| 重试耗尽 | {manifest.get('validation_retry_exhausted_count', 0)} |",
        f"| Provider 传输重试 | {timing.get('transport_retry_count', 0)} |",
        f"| 初次 API 平均延迟 | {timing.get('initial_average_ms', 0) / 1000:.1f}s |",
        f"| 重试 API 平均延迟 | {timing.get('retry_average_ms', 0) / 1000:.1f}s |",
        "",
    ]
    for title, key in (
        ("MatchFeature types", "match_feature_types"),
        ("Position classification status", "position_classifications"),
        ("Skill item_type", "item_types"),
        ("Skill proficiency", "proficiency_levels"),
        ("Review flags", "review_flags"),
        ("Top unresolved skill names", "unresolved_items"),
        ("硬失败", "failures"),
    ):
        lines.extend([f"## {title}", ""])
        values = summary.get(key, [])
        if values:
            lines.extend(["| 类型 | 数量 |", "|---|---:|"])
            lines.extend(f"| {name} | {count} |" for name, count in values)
        else:
            lines.append("无。")
        lines.append("")
    lines.extend(
        [
            "## Artifact integrity",
            "",
            "| Check | Status |",
            "|---|---|",
            *[
                f"| {name} | {'PASS' if passed else 'FAIL'} |"
                for name, passed in summary.get("integrity_checks", {}).items()
            ],
            "",
            "## 解释边界",
            "",
            "- 本报告统计抽取层、归一化层、review flag 和硬失败，不推断未建立金标的内容准确率。",
            "- Evidence 只有精确对齐后才能进入正式结果。",
            "- 标准技能 ID 属于归一化层，不由抽取模型生成。",
            "- 经历—能力验证属于 MatchFeature 之后的独立派生层；有直接证据时只提供非负加分，无经历证据不扣分。",
            "",
        ]
    )
    return "\n".join(lines)


def generate_run_report(run_dir: Path, report_path: Path | None = None) -> Path:
    output = report_path or run_dir / "research_report.md"
    output.write_text(generate_report(summarize_run(run_dir)), encoding="utf-8")
    return output
