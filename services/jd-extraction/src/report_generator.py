from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .validator import BUSINESS_VALIDATOR_VERSION

REPORT_GENERATOR_VERSION = "2.8"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


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
    flags = read_jsonl(final / "review_flags.jsonl")
    failures = read_jsonl(final / "failed_cases.jsonl")
    illegal = read_jsonl(final / "illegal_enum_cases.jsonl")
    resolution_summary_path = final / "normalization_resolution_summary.json"
    resolution_summary = (
        read_json(resolution_summary_path)
        if resolution_summary_path.exists()
        else None
    )
    logs = read_jsonl(run_dir / "logs.jsonl")
    last_run_started = max(
        (
            index
            for index, event in enumerate(logs)
            if event.get("event_type") == "run_started"
        ),
        default=0,
    )
    logs = logs[last_run_started:]
    api_attempts = [event for event in logs if event.get("event_type") == "api_attempt_finished"]
    initial_api_attempts = [event for event in api_attempts if event.get("mode") == "initial"]
    retry_api_attempts = [event for event in api_attempts if event.get("mode") != "initial"]

    def average_elapsed_ms(events: list[dict[str, Any]]) -> float:
        return round(
            sum(float(event.get("elapsed_ms", 0)) for event in events) / len(events),
            1,
        ) if events else 0.0
    kinds: Counter[str] = Counter()
    modalities: Counter[str] = Counter()
    item_types: Counter[str] = Counter()
    responsibility_count = requirement_count = skill_count = 0
    company_fact_count = employment_fact_count = 0
    unresolved_item_count = sum(len(record.get("unresolved_items", [])) for record in normalized)
    flagged_document_count = len({flag.get("jd_id") for flag in flags if flag.get("jd_id")})
    normalized_skill_count = 0
    resolved_skill_count = 0
    job_classifications: Counter[str] = Counter()
    unresolved_items: Counter[str] = Counter()
    employment_other_count = 0
    company_other_count = 0
    employment_fact_counts: list[int] = []
    for record in normalized:
        classification = record.get("job_classification") or {}
        if classification.get("schema_version") == "job-position-classification.v3":
            status = classification.get("classification_status", "missing")
        else:
            status = classification.get("resolution_status", "missing")
        job_classifications[status] += 1
        for requirement in record.get("normalized_requirements", []):
            for skill in requirement.get("skills", []):
                normalized_skill_count += 1
                if skill.get("identity_resolution_status") == "resolved":
                    resolved_skill_count += 1
        unresolved_items.update(record.get("unresolved_items", []))
    annotation_ids = [record.get("document_id") for record in annotations]
    normalized_ids = [record.get("document_id") for record in normalized]
    failed_ids = [record.get("jd_id") for record in failures]
    annotation_id_set = {value for value in annotation_ids if value}
    normalized_id_set = {value for value in normalized_ids if value}
    failed_id_set = {value for value in failed_ids if value}
    requirement_ids_by_document = {
        record.get("document_id"): {
            requirement.get("requirement_id")
            for requirement in [*record.get("responsibilities", []), *record.get("requirements", [])]
        }
        for record in annotations
        if record.get("document_id")
    }
    fact_ids_by_document = {
        record.get("document_id"): {
            fact.get("fact_id")
            for fact in [*record.get("company_facts", []), *record.get("employment_facts", [])]
        }
        for record in annotations
        if record.get("document_id")
    }

    def review_flag_reference_exists(flag: dict[str, Any]) -> bool:
        document_id = flag.get("jd_id")
        if document_id not in annotation_id_set:
            return False
        scope = flag.get("rule_scope")
        if scope == "document":
            return True
        if scope == "fact":
            return flag.get("item_id") in fact_ids_by_document.get(document_id, set())
        return flag.get("requirement_id") in requirement_ids_by_document.get(document_id, set())
    candidate_requirement_ids_by_document = {
        record.get("document_id"): {
            requirement.get("requirement_id") for requirement in record.get("requirements", [])
        }
        for record in annotations
        if record.get("document_id")
    }
    normalized_requirement_ids_by_document = {
        record.get("document_id"): {
            requirement.get("requirement_id") for requirement in record.get("normalized_requirements", [])
        }
        for record in normalized
        if record.get("document_id")
    }
    integrity_checks = {
        "annotations_match_manifest_success": len(annotations) == manifest.get("success_count", 0),
        "normalized_match_annotations": len(normalized) == len(annotations),
        "failures_match_manifest_failed": len(failures) == manifest.get("failed_count", 0),
        "review_flags_match_manifest": len(flags) == manifest.get("review_flag_count", 0),
        "annotation_document_ids_unique": len(annotation_ids) == len(annotation_id_set),
        "normalized_document_ids_match_annotations": normalized_id_set == annotation_id_set,
        "success_and_failure_document_ids_disjoint": annotation_id_set.isdisjoint(failed_id_set),
        "review_flags_reference_existing_objects": all(review_flag_reference_exists(flag) for flag in flags),
        "normalized_requirement_ids_match_annotations": all(
            normalized_requirement_ids_by_document.get(document_id, set()) == requirement_ids
            for document_id, requirement_ids in candidate_requirement_ids_by_document.items()
        ),
        "business_validator_version_matches": manifest.get("business_validator_version") == BUSINESS_VALIDATOR_VERSION,
        "report_generator_version_matches": manifest.get("report_generator_version") == REPORT_GENERATOR_VERSION,
        "api_attempt_events_match_manifest": len(api_attempts) == manifest.get("api_call_count", 0),
    }
    for annotation in annotations:
        responsibilities = annotation.get("responsibilities", [])
        requirements = annotation.get("requirements", [])
        responsibility_count += len(responsibilities)
        requirement_count += len(requirements)
        company_fact_count += len(annotation.get("company_facts", []))
        employment_fact_count += len(annotation.get("employment_facts", []))
        employment_fact_counts.append(len(annotation.get("employment_facts", [])))
        company_other_count += sum(fact.get("kind") == "other" for fact in annotation.get("company_facts", []))
        employment_other_count += sum(
            fact.get("kind") == "other" for fact in annotation.get("employment_facts", [])
        )
        for requirement in [*responsibilities, *requirements]:
            kinds[requirement.get("kind", "unknown")] += 1
            modalities[requirement.get("modality", "unknown")] += 1
            for item in requirement.get("items", []):
                skill_count += 1
                item_types[item.get("item_type", "other")] += 1
    return {
        "manifest": manifest,
        "counts": {
            "annotations": len(annotations),
            "normalized_results": len(normalized),
            "responsibilities": responsibility_count,
            "requirements": requirement_count,
            "skills": skill_count,
            "company_facts": company_fact_count,
            "employment_facts": employment_fact_count,
            "unresolved_items": unresolved_item_count,
            "flagged_documents": flagged_document_count,
            "normalized_skills": normalized_skill_count,
            "resolved_skills": resolved_skill_count,
            "company_other_facts": company_other_count,
            "employment_other_facts": employment_other_count,
            "max_employment_facts_per_document": max(employment_fact_counts, default=0),
        },
        "kinds": kinds.most_common(),
        "modalities": modalities.most_common(),
        "item_types": item_types.most_common(),
        "review_flags": Counter(flag.get("issue_type") for flag in flags).most_common(),
        "job_classifications": job_classifications.most_common(),
        "unresolved_items": unresolved_items.most_common(30),
        "normalization_dispositions": (
            resolution_summary.get("counts", {})
            if isinstance(resolution_summary, dict)
            else {}
        ),
        "failures": Counter(case.get("error_type") for case in failures).most_common(),
        "failed_cases": failures,
        "illegal_enums": illegal,
        "api_timing": {
            "attempt_count": len(api_attempts),
            "initial_average_ms": average_elapsed_ms(initial_api_attempts),
            "initial_max_ms": max((float(event.get("elapsed_ms", 0)) for event in initial_api_attempts), default=0.0),
            "retry_average_ms": average_elapsed_ms(retry_api_attempts),
            "retry_max_ms": max((float(event.get("elapsed_ms", 0)) for event in retry_api_attempts), default=0.0),
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
    api_timing = summary.get("api_timing", {})
    lines = [
        "# JD 抽取运行研究报告",
        "",
        "## 总览",
        "",
        f"本轮处理 {total} 条 JD，成功 {success} 条，失败 {failed} 条，成功率 {rate}。",
        "该成功率仅表示流水线通过率，不表示抽取准确率或召回率。",
        "",
        "| 指标 | 数量 |",
        "|---|---:|",
        f"| Responsibilities | {counts['responsibilities']} |",
        f"| Candidate requirements | {counts['requirements']} |",
        f"| Skill items | {counts['skills']} |",
        f"| Company facts | {counts['company_facts']} |",
        f"| Employment facts | {counts['employment_facts']} |",
        f"| Unresolved normalization items | {counts.get('unresolved_items', 0)} |",
        f"| Documents with review flags | {counts.get('flagged_documents', 0)} |",
        f"| Hard failure rate | {failure_rate} |",
        f"| Review-flagged document rate among successes | {flagged_rate} |",
        f"| Skill normalization coverage | {resolved_skills}/{normalized_skills} ({normalization_rate}) |",
        f"| Validation retries | {manifest.get('validation_retry_count', 0)} |",
        f"| Initial API average latency | {api_timing.get('initial_average_ms', 0) / 1000:.1f}s |",
        f"| Initial API max latency | {api_timing.get('initial_max_ms', 0) / 1000:.1f}s |",
        f"| Retry API average latency | {api_timing.get('retry_average_ms', 0) / 1000:.1f}s |",
        f"| Retry API max latency | {api_timing.get('retry_max_ms', 0) / 1000:.1f}s |",
        f"| Local repair calls | {manifest.get('local_repair_count', 0)} |",
        f"| Full re-extraction calls | {manifest.get('full_reextract_count', 0)} |",
        f"| Rejected local repair protocols | {manifest.get('local_repair_protocol_rejected_count', 0)} |",
        f"| JDs recovered by local repair | {manifest.get('local_repair_recovered_count', 0)} |",
        f"| JDs recovered by validation retry | {manifest.get('validation_retry_recovered_count', 0)} |",
        f"| Initial-pass JDs | {manifest.get('initial_pass_count', 0)} |",
        f"| Recovered after first retry | {manifest.get('recovered_after_first_retry_count', 0)} |",
        f"| Recovered after second retry | {manifest.get('recovered_after_second_retry_count', 0)} |",
        f"| Exhausted validation retries | {manifest.get('validation_retry_exhausted_count', 0)} |",
        f"| Deterministic authoritative corrections | {manifest.get('deterministic_correction_count', 0)} |",
        f"| Company facts with kind=other | {counts.get('company_other_facts', 0)} |",
        f"| Employment facts with kind=other | {counts.get('employment_other_facts', 0)} |",
        f"| Max employment facts in one JD | {counts.get('max_employment_facts_per_document', 0)} |",
        "",
    ]
    dispositions = summary.get("normalization_dispositions", {})
    if dispositions:
        lines.extend(
            [
                "",
                "## Normalization dispositions",
                "",
                "| Disposition | Occurrences |",
                "| --- | ---: |",
                *[
                    f"| {name} | {count} |"
                    for name, count in dispositions.items()
                ],
            ]
        )
    if manifest.get("post_review_applied_at"):
        lines.extend([
            "## Post-review revision",
            "",
            f"- Applied at: {manifest['post_review_applied_at']}",
            f"- Decision file: {manifest.get('post_review_decision_path', '')}",
            f"- Applied decisions: {manifest.get('post_review_applied_decision_count', 0)}",
            f"- Acknowledged review flags: {manifest.get('post_review_acknowledged_flag_count', 0)}",
            f"- Post-review authoritative corrections: {manifest.get('post_review_correction_count', 0)}",
            "- Existing extraction evidence was revalidated and renormalized without another model extraction.",
            "",
        ])
    for title, key in (
        ("Requirement kind", "kinds"),
        ("Modality", "modalities"),
        ("Skill item_type", "item_types"),
        ("Review flags", "review_flags"),
        ("Job classification status", "job_classifications"),
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
    failed_cases = summary.get("failed_cases", [])
    lines.extend(["## 硬失败明细", ""])
    if failed_cases:
        lines.extend(["| JD | Stage | Error | Message |", "|---|---|---|---|"])
        for case in failed_cases:
            message = " ".join(str(case.get("error_message", "")).split())
            if len(message) > 180:
                message = message[:177] + "..."
            message = message.replace("|", "\\|")
            lines.append(
                f"| {case.get('jd_id', '')} | {case.get('stage', '')} | "
                f"{case.get('error_type', '')} | {message} |"
            )
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
            "- 本报告只统计抽取层、归一化层、review flag 和硬失败，不推断未建立金标的召回率。",
            "- Artifact integrity 只验证文件、ID、哈希和计数一致性，不代表内容质量通过。",
            "- Validation retry budget 按每条 JD 独立计算；Exhausted validation retries 表示该 JD 已用完自己的预算。",
            "- Evidence 只有精确对齐后才能进入正式结果。",
            "- 标准技能 ID、岗位族和导出细类属于归一化层，不由抽取模型生成。",
            "- 若存在 Post-review revision，本报告中的归一化与 review flag 反映人工审查后的最终状态，原始模型调用指标保持不变。",
            "",
        ]
    )
    return "\n".join(lines)


def generate_run_report(run_dir: Path, report_path: Path | None = None) -> Path:
    output = report_path or run_dir / "research_report.md"
    output.write_text(generate_report(summarize_run(run_dir)), encoding="utf-8")
    return output
