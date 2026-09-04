from __future__ import annotations

import copy
import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class CleaningDecision:
    action: str
    reason: str
    position_code: str | None = None


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSONL at {path}:{line_number}: {exc}"
                ) from exc
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        for row in rows
    )
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def normalize_title(value: str) -> str:
    return re.sub(
        r"[\s_\-—/·()（）【】\[\],，。:：;；]+",
        "",
        value.strip().lower(),
    )


def load_catalog(path: Path) -> dict[str, Any]:
    catalog = read_json(path)
    if catalog.get("schema") != "position-taxonomy-catalog.v3":
        raise ValueError("position catalog must use position-taxonomy-catalog.v3")
    positions = catalog.get("positions")
    families = catalog.get("families")
    if not isinstance(positions, list) or not isinstance(families, list):
        raise ValueError("position catalog has invalid positions or families")
    position_by_code = {str(item["code"]): item for item in positions}
    family_by_code = {str(item["code"]): item for item in families}
    if len(position_by_code) != len(positions):
        raise ValueError("position catalog contains duplicate codes")
    catalog["_position_by_code"] = position_by_code
    catalog["_family_by_code"] = family_by_code
    return catalog


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != "position-cleaning-policy.v1":
        raise ValueError("position cleaning policy schema is invalid")
    return policy


def discover_run_dirs(
    consolidated_run: Path,
    split_runs_root: Path,
) -> list[Path]:
    runs = [consolidated_run]
    runs.extend(
        sorted(
            path
            for path in split_runs_root.iterdir()
            if path.is_dir()
            and (path / "final" / "annotations.jsonl").is_file()
            and (path / "final" / "normalized_annotations.jsonl").is_file()
        )
    )
    return runs


def load_source_records(
    run_dirs: Iterable[Path],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    annotations: dict[str, dict[str, Any]] = {}
    normalized: dict[str, dict[str, Any]] = {}
    review_flags: list[dict[str, Any]] = []
    for run in run_dirs:
        for row in read_jsonl(run / "final" / "annotations.jsonl"):
            document_id = str(row.get("document_id") or "")
            if not document_id or document_id in annotations:
                raise ValueError(f"invalid or duplicate annotation: {document_id}")
            annotations[document_id] = row
        for row in read_jsonl(run / "final" / "normalized_annotations.jsonl"):
            document_id = str(row.get("document_id") or "")
            if not document_id or document_id in normalized:
                raise ValueError(f"invalid or duplicate normalization: {document_id}")
            normalized[document_id] = row
        review_flags.extend(read_jsonl(run / "final" / "review_flags.jsonl"))
    if set(annotations) != set(normalized):
        missing_annotations = sorted(set(normalized) - set(annotations))
        missing_normalized = sorted(set(annotations) - set(normalized))
        raise ValueError(
            "annotation and normalization identities differ: "
            f"missing_annotations={missing_annotations[:5]}, "
            f"missing_normalized={missing_normalized[:5]}"
        )
    return annotations, normalized, review_flags


def load_reviewed_package(
    package_path: Path,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    with zipfile.ZipFile(package_path) as archive:
        annotations_rows = json.loads(
            archive.read("final/annotations_nested.json")
        )
        normalized_rows = json.loads(
            archive.read("final/normalized_annotations.json")
        )
    annotations = {
        str(row["document_id"]): row
        for row in annotations_rows
    }
    normalized = {
        str(row["document_id"]): row
        for row in normalized_rows
    }
    if len(annotations) != len(annotations_rows):
        raise ValueError("reviewed package contains duplicate annotations")
    if len(normalized) != len(normalized_rows):
        raise ValueError("reviewed package contains duplicate normalizations")
    if set(annotations) != set(normalized):
        raise ValueError(
            "reviewed package annotation and normalization identities differ"
        )
    return annotations, normalized, []


def overlay_classification_baseline(
    normalized: dict[str, dict[str, Any]],
    package_path: Path,
) -> None:
    with zipfile.ZipFile(package_path) as archive:
        matching = [
            name
            for name in archive.namelist()
            if name.endswith("/final/normalized_annotations.jsonl")
        ]
        if len(matching) != 1:
            raise ValueError(
                "classification baseline package must contain exactly one "
                "final/normalized_annotations.jsonl"
            )
        rows = [
            json.loads(line)
            for line in archive.read(matching[0]).decode("utf-8").split("\n")
            if line.strip()
        ]
    for row in rows:
        document_id = str(row.get("document_id") or "")
        classification = row.get("job_classification")
        if document_id in normalized and classification:
            normalized[document_id]["job_classification"] = copy.deepcopy(
                classification
            )


def build_title_indexes(
    normalized: dict[str, dict[str, Any]],
    catalog: dict[str, Any],
) -> tuple[dict[str, set[str]], dict[str, Counter[str]]]:
    alias_codes: dict[str, set[str]] = defaultdict(set)
    for item in catalog["positions"]:
        for value in [item.get("name"), *(item.get("aliases") or [])]:
            if value:
                alias_codes[normalize_title(str(value))].add(str(item["code"]))
    resolved_titles: dict[str, Counter[str]] = defaultdict(Counter)
    for row in normalized.values():
        classification = row.get("job_classification") or {}
        if classification.get("classification_status") not in {
            "resolved",
            "manually_confirmed",
        }:
            continue
        position_code = classification.get("position_code")
        title = classification.get("source_title")
        if position_code and title:
            resolved_titles[normalize_title(str(title))][str(position_code)] += 1
    return alias_codes, resolved_titles


def build_candidate_title_consensus(
    normalized: dict[str, dict[str, Any]],
    resolved_titles: dict[str, Counter[str]],
    policy: dict[str, Any],
) -> dict[str, str]:
    candidate_votes: dict[str, Counter[str]] = defaultdict(Counter)
    title_occurrences: Counter[str] = Counter()
    for row in normalized.values():
        classification = row.get("job_classification") or {}
        if classification.get("classification_status") not in {
            "ambiguous",
            "catalog_gap",
        }:
            continue
        title_key = normalize_title(
            str(classification.get("source_title") or "")
        )
        candidates = classification.get("candidate_positions") or []
        if not title_key or not candidates:
            continue
        top_code = str(candidates[0].get("position_code") or "")
        if not top_code:
            continue
        title_occurrences[title_key] += 1
        candidate_votes[title_key][top_code] += 1

    settings = policy.get("candidate_title_consensus") or {}
    minimum_occurrences = int(
        settings.get("minimum_title_occurrences", 2)
    )
    minimum_share = float(settings.get("minimum_winner_share", 0.75))
    result = {}
    for title_key, votes in candidate_votes.items():
        if title_occurrences[title_key] < minimum_occurrences:
            continue
        combined = votes + resolved_titles.get(title_key, Counter())
        if not combined:
            continue
        winner, winner_count = combined.most_common(1)[0]
        if winner_count / combined.total() >= minimum_share:
            result[title_key] = winner
    return result


def _candidate_values(
    classification: dict[str, Any],
) -> tuple[str | None, float | None, float | None]:
    candidates = classification.get("candidate_positions") or []
    if not candidates:
        return None, None, None
    top = candidates[0]
    top_code = str(top.get("position_code") or "") or None
    top_score = float(top["score"]) if top.get("score") is not None else None
    second_score = (
        float(candidates[1]["score"])
        if len(candidates) > 1 and candidates[1].get("score") is not None
        else None
    )
    return top_code, top_score, second_score


def _has_pattern(value: str, patterns: Iterable[str]) -> bool:
    lowered = value.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def _strong_title_position(
    title: str,
    policy: dict[str, Any],
) -> str | None:
    lowered = title.lower()
    matches = []
    for rule in policy.get("strong_title_rules") or []:
        required_any = rule.get("required_any") or []
        required_all = rule.get("required_all") or []
        forbidden_any = rule.get("forbidden_any") or []
        if required_any and not _has_pattern(lowered, required_any):
            continue
        if required_all and not all(
            pattern.lower() in lowered for pattern in required_all
        ):
            continue
        if forbidden_any and _has_pattern(lowered, forbidden_any):
            continue
        matches.append(str(rule["position_code"]))
    return matches[0] if len(set(matches)) == 1 else None


def _annotation_title_evidence_ref(
    annotation: dict[str, Any],
) -> str | None:
    evidence = (annotation.get("job_title") or {}).get("evidence") or {}
    return str(evidence.get("source_id") or "") or None


def decide_record(
    annotation: dict[str, Any],
    normalized: dict[str, Any],
    *,
    policy: dict[str, Any],
    alias_codes: dict[str, set[str]],
    resolved_titles: dict[str, Counter[str]],
    candidate_title_consensus: dict[str, str] | None = None,
) -> CleaningDecision:
    classification = normalized.get("job_classification") or {}
    status = str(classification.get("classification_status") or "missing")
    if status in {"resolved", "manually_confirmed"}:
        return CleaningDecision("keep", "already_publishable")

    title = str(
        classification.get("source_title")
        or (annotation.get("job_title") or {}).get("value")
        or ""
    )
    title_key = normalize_title(title)
    top_code, top_score, second_score = _candidate_values(classification)
    evidence_refs = classification.get("evidence_refs") or []

    if status == "catalog_gap" and top_code:
        candidates = classification.get("candidate_positions") or []
        settings = policy.get("single_candidate_catalog_gap") or {}
        if (
            len(candidates) == 1
            and top_score is not None
            and top_score >= float(settings.get("minimum_score", 1))
            and len(evidence_refs)
            >= int(settings.get("minimum_evidence_refs", 1))
        ):
            return CleaningDecision(
                "promote",
                "single_candidate_catalog_gap_with_evidence",
                top_code,
            )

    if status == "ambiguous" and top_code and evidence_refs:
        near = policy["near_threshold"]
        margin = (
            top_score - second_score
            if top_score is not None and second_score is not None
            else 1.0
        )
        if (
            top_score is not None
            and top_score >= float(near["minimum_top_score"])
            and margin >= float(near["minimum_margin"])
            and len(evidence_refs) >= int(near["minimum_evidence_refs"])
        ):
            return CleaningDecision(
                "promote",
                "near_threshold_with_evidence",
                top_code,
            )
        codes = alias_codes.get(title_key, set())
        if len(codes) == 1 and top_code in codes:
            return CleaningDecision(
                "promote",
                "unique_catalog_alias_matches_top_candidate",
                top_code,
            )
        historical = resolved_titles.get(title_key, Counter())
        minimum_occurrences = int(
            (policy.get("historical_title_consensus") or {}).get(
                "minimum_occurrences",
                1,
            )
        )
        if (
            len(historical) == 1
            and historical.total() >= minimum_occurrences
            and next(iter(historical)) == top_code
        ):
            return CleaningDecision(
                "promote",
                "unique_historical_title_matches_top_candidate",
                top_code,
            )
        consensus_code = (candidate_title_consensus or {}).get(title_key)
        if consensus_code == top_code:
            return CleaningDecision(
                "promote",
                "candidate_title_consensus_matches_top_candidate",
                top_code,
            )

    strong_title_code = _strong_title_position(title, policy)
    candidate_codes = {
        str(candidate.get("position_code"))
        for candidate in classification.get("candidate_positions") or []
        if candidate.get("position_code")
    }
    title_evidence_ref = _annotation_title_evidence_ref(annotation)
    if (
        strong_title_code
        and title_evidence_ref
        and (
            (
                status in {"ambiguous", "catalog_gap"}
                and strong_title_code in candidate_codes
            )
            or (
                status == "out_of_scope"
                and not candidate_codes
            )
        )
    ):
        return CleaningDecision(
            "promote",
            "strong_catalog_title_with_annotation_evidence",
            strong_title_code,
        )

    if status == "out_of_scope":
        if strong_title_code and title_evidence_ref and not candidate_codes:
            return CleaningDecision(
                "promote",
                "strong_catalog_title_with_annotation_evidence",
                strong_title_code,
            )
        candidates = classification.get("candidate_positions") or []
        domains = classification.get("observed_skill_domain_codes") or []
        title_is_technical = _has_pattern(
            title, policy["technical_title_patterns"]
        )
        title_is_obvious_business = _has_pattern(
            title, policy["obvious_out_of_scope_title_patterns"]
        )
        if title_is_obvious_business and not candidates and not domains:
            return CleaningDecision("delete", "obvious_non_technical_title")
        if not candidates and not domains and not title_is_technical:
            return CleaningDecision("delete", "no_catalog_or_technical_signal")
        return CleaningDecision("quarantine", "possible_technical_out_of_scope")

    return CleaningDecision("quarantine", f"unresolved_{status}")


def promote_classification(
    normalized: dict[str, Any],
    *,
    decision: CleaningDecision,
    catalog: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(normalized)
    classification = copy.deepcopy(result.get("job_classification") or {})
    position = catalog["_position_by_code"].get(decision.position_code)
    if position is None:
        raise ValueError(f"unknown promoted position: {decision.position_code}")
    family = catalog["_family_by_code"].get(position["family_code"])
    if family is None:
        raise ValueError(f"unknown promoted family: {position['family_code']}")
    candidates = classification.get("candidate_positions") or []
    matched_candidate = next(
        (
            candidate
            for candidate in candidates
            if candidate.get("position_code") == decision.position_code
        ),
        None,
    )
    title_evidence_ref = _annotation_title_evidence_ref(
        result.get("_source_annotation") or {}
    )
    if matched_candidate is None and title_evidence_ref is None:
        raise ValueError(
            f"promoted position lacks candidate or title evidence: "
            f"{result.get('document_id')}"
        )
    evidence_refs = list(classification.get("evidence_refs") or [])
    if not evidence_refs and title_evidence_ref:
        evidence_refs.append(title_evidence_ref)
    classification.update(
        {
            "position_code": position["code"],
            "position_name": position["name"],
            "family_code": family["code"],
            "family_name": family["name"],
            "classification_status": "manually_confirmed",
            "confidence": max(
                float(classification.get("confidence") or 0),
                float((matched_candidate or {}).get("score") or 0),
                0.9 if matched_candidate is None else 0,
            ),
            "review_reason_codes": [
                f"LOCAL_CLEANING_{decision.reason.upper()}"
            ],
            "classification_policy_version": policy["policy_version"],
            "evidence_refs": evidence_refs,
        }
    )
    result["job_classification"] = classification
    result.pop("_source_annotation", None)
    return result


def _filter_review_flags(
    review_flags: Iterable[dict[str, Any]],
    retained_ids: set[str],
    promoted_ids: set[str],
) -> list[dict[str, Any]]:
    result = []
    for flag in review_flags:
        document_id = str(flag.get("jd_id") or flag.get("document_id") or "")
        if document_id not in retained_ids:
            continue
        if (
            document_id in promoted_ids
            and flag.get("issue_type") == "job_classification_not_resolved"
        ):
            continue
        result.append(flag)
    return result


def clean_position_dataset(
    *,
    run_dirs: Iterable[Path] | None,
    catalog_path: Path,
    policy_path: Path,
    output_dir: Path,
    current_document_ids: set[str] | None = None,
    reviewed_package_path: Path | None = None,
    classification_baseline_path: Path | None = None,
    publish_only: bool = False,
) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError(f"output directory already exists: {output_dir}")
    catalog = load_catalog(catalog_path)
    policy = load_policy(policy_path)
    if reviewed_package_path is not None:
        annotations, normalized, review_flags = load_reviewed_package(
            reviewed_package_path
        )
    elif run_dirs is not None:
        annotations, normalized, review_flags = load_source_records(run_dirs)
    else:
        raise ValueError("run_dirs or reviewed_package_path is required")
    if classification_baseline_path is not None:
        overlay_classification_baseline(
            normalized,
            classification_baseline_path,
        )
    source_ids = set(annotations)
    selected_ids = source_ids if current_document_ids is None else current_document_ids
    missing_current = sorted(selected_ids - source_ids)
    if missing_current:
        raise ValueError(
            f"current document ids are absent from source outputs: {missing_current[:5]}"
        )
    selected_ids = source_ids & selected_ids
    alias_codes, resolved_titles = build_title_indexes(normalized, catalog)
    candidate_title_consensus = build_candidate_title_consensus(
        normalized,
        resolved_titles,
        policy,
    )

    clean_annotations: list[dict[str, Any]] = []
    clean_normalized: list[dict[str, Any]] = []
    deleted: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    promoted_ids: set[str] = set()
    action_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    status_before: Counter[str] = Counter()
    status_after: Counter[str] = Counter()

    for document_id in sorted(selected_ids):
        annotation = annotations[document_id]
        normalization = normalized[document_id]
        classification = normalization.get("job_classification") or {}
        original_status = str(classification.get("classification_status") or "missing")
        status_before[original_status] += 1
        decision = decide_record(
            annotation,
            normalization,
            policy=policy,
            alias_codes=alias_codes,
            resolved_titles=resolved_titles,
            candidate_title_consensus=candidate_title_consensus,
        )
        action_counts[decision.action] += 1
        reason_counts[decision.reason] += 1
        decision_row = {
            "document_id": document_id,
            "source_title": classification.get("source_title"),
            "original_status": original_status,
            "action": decision.action,
            "reason": decision.reason,
            "position_code": decision.position_code,
        }
        decisions.append(decision_row)
        if decision.action == "delete":
            deleted.append(decision_row)
            continue
        if decision.action == "quarantine":
            quarantine.append(
                {
                    **decision_row,
                    "annotation": annotation,
                    "normalization": normalization,
                }
            )
            continue
        output_normalization = normalization
        if decision.action == "promote":
            normalization_with_source = copy.deepcopy(normalization)
            normalization_with_source["_source_annotation"] = annotation
            output_normalization = promote_classification(
                normalization_with_source,
                decision=decision,
                catalog=catalog,
                policy=policy,
            )
            promoted_ids.add(document_id)
        clean_annotations.append(annotation)
        clean_normalized.append(output_normalization)
        output_status = str(
            (output_normalization.get("job_classification") or {}).get(
                "classification_status"
            )
            or "missing"
        )
        status_after[output_status] += 1

    retained_ids = {
        str(row["document_id"])
        for row in clean_annotations
    }
    clean_review_flags = _filter_review_flags(
        review_flags,
        retained_ids,
        promoted_ids,
    )
    final_dir = output_dir / "final"
    audit_dir = output_dir / "audit"
    quarantine_dir = output_dir / "quarantine"
    write_jsonl(final_dir / "annotations.jsonl", clean_annotations)
    write_json(final_dir / "annotations_nested.json", clean_annotations)
    write_jsonl(final_dir / "normalized_annotations.jsonl", clean_normalized)
    write_json(final_dir / "normalized_annotations.json", clean_normalized)
    write_jsonl(final_dir / "review_flags.jsonl", clean_review_flags)
    write_jsonl(final_dir / "failed_cases.jsonl", [])
    write_jsonl(final_dir / "illegal_enum_cases.jsonl", [])
    if not publish_only:
        write_jsonl(
            audit_dir / "cleaning_decisions.jsonl",
            [row for row in decisions if row["action"] != "delete"],
        )
        write_jsonl(
            quarantine_dir / "possible_technical_out_of_scope.jsonl",
            [
                row
                for row in quarantine
                if row["reason"] == "possible_technical_out_of_scope"
            ],
        )
        write_jsonl(
            quarantine_dir / "unresolved_classifications.jsonl",
            [
                row
                for row in quarantine
                if row["reason"].startswith("unresolved_")
            ],
        )
    write_json(
        audit_dir / "current_document_ids.json",
        sorted(
            {
                str(row["document_id"])
                for row in clean_annotations
            }
            | (
                set()
                if publish_only
                else {
                    str(row["document_id"])
                    for row in quarantine
                }
            )
        ),
    )

    archive_path = output_dir.with_suffix(".zip")
    if archive_path.exists():
        raise ValueError(f"output archive already exists: {archive_path}")
    position_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    for row in clean_normalized:
        classification = row.get("job_classification") or {}
        position_code = classification.get("position_code")
        family_code = classification.get("family_code")
        if position_code:
            position_counts[str(position_code)] += 1
        if family_code:
            family_counts[str(family_code)] += 1
    zero_sample_codes = sorted(
        set(catalog["_position_by_code"]) - set(position_counts)
    )
    low_frequency = {
        "one_or_two": dict(
            sorted(
                (code, count)
                for code, count in position_counts.items()
                if count <= 2
            )
        ),
        "three_to_five": dict(
            sorted(
                (code, count)
                for code, count in position_counts.items()
                if 3 <= count <= 5
            )
        ),
        "six_to_ten": dict(
            sorted(
                (code, count)
                for code, count in position_counts.items()
                if 6 <= count <= 10
            )
        ),
    }
    distribution = {
        "schema": "position-cleaned-distribution.v1",
        "publishable_record_count": len(clean_normalized),
        "represented_position_count": len(position_counts),
        "catalog_position_count": len(catalog["_position_by_code"]),
        "position_counts": dict(
            sorted(position_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "family_counts": dict(
            sorted(family_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "zero_sample_position_codes": zero_sample_codes,
        "low_frequency_position_counts": low_frequency,
    }
    write_json(audit_dir / "position_distribution.json", distribution)

    report = {
        "schema": "position-cleaned-dataset-report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": policy["policy_version"],
        "taxonomy_version": catalog["catalog_version"],
        "source_record_count": len(source_ids),
        "selected_record_count": len(selected_ids),
        "retained_record_count": len(clean_annotations),
        "deleted_record_count": len(deleted),
        "quarantined_record_count": len(quarantine),
        "promoted_record_count": len(promoted_ids),
        "publishable_record_count": len(clean_annotations),
        "represented_position_count": len(position_counts),
        "zero_sample_position_count": len(zero_sample_codes),
        "action_counts": dict(sorted(action_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "classification_status_before": dict(sorted(status_before.items())),
        "classification_status_after": dict(sorted(status_after.items())),
        "review_flag_count": len(clean_review_flags),
        "original_outputs_modified": False,
        "package_mode": "publish_only" if publish_only else "audit",
        "source_kind": (
            "reviewed_extraction_audit_package"
            if reviewed_package_path is not None
            else "local_run_directories"
        ),
        "source_package": (
            reviewed_package_path.name
            if reviewed_package_path is not None
            else None
        ),
        "classification_baseline_package": (
            classification_baseline_path.name
            if classification_baseline_path is not None
            else None
        ),
        "archive_path": archive_path.name,
    }
    write_json(output_dir / "cleaning_report.json", report)
    manifest = {
        "schema": "jd-position-cleaned-bundle.v1",
        "created_at": report["generated_at"],
        "record_count": len(clean_annotations),
        "position_taxonomy_version": catalog["catalog_version"],
        "position_cleaning_policy_version": policy["policy_version"],
        "classification_status_counts": report["classification_status_after"],
        "final_files": [
            "final/annotations.jsonl",
            "final/annotations_nested.json",
            "final/normalized_annotations.jsonl",
            "final/normalized_annotations.json",
            "final/review_flags.jsonl",
            "final/failed_cases.jsonl",
            "final/illegal_enum_cases.jsonl",
        ],
        "audit_files": (
            [
                "audit/current_document_ids.json",
                "audit/position_distribution.json",
                "cleaning_report.json",
            ]
            if publish_only
            else [
                "audit/cleaning_decisions.jsonl",
                "audit/current_document_ids.json",
                "audit/position_distribution.json",
                "quarantine/possible_technical_out_of_scope.jsonl",
                "quarantine/unresolved_classifications.jsonl",
                "cleaning_report.json",
            ]
        ),
    }
    write_json(output_dir / "manifest.json", manifest)
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir.parent))
    return report
