from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The helper is materialized in this service so the CV batch entrypoint does
# not depend on the caller's working directory or another service checkout.
from scripts.reclassify_job_positions import (  # noqa: E402
    _checkpoint,
    _load_api_key,
    _read_json,
    _request_validated_batch,
    _validate_catalog,
    _validate_decisions,
    _write_json_atomic,
    _write_jsonl_atomic,
)
from src.capability_verification import (  # noqa: E402
    CAPABILITY_VERIFICATION_DERIVATION_VERSION,
    build_capability_verification,
)
from src.exporter import (  # noqa: E402
    export_capability_evidence_links_jsonl,
    export_capability_profiles_jsonl,
    export_capability_verification_profiles,
)
from src.models import CVMatchFeatureResult  # noqa: E402
from src.report_generator import (  # noqa: E402
    REPORT_GENERATOR_VERSION,
    generate_run_report,
)


def _fact_texts(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    values: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            values.append(item.strip())
        elif isinstance(item, dict):
            value = item.get("value") or item.get("text")
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
    return values


def _document_context(
    annotation: dict[str, Any], normalized: dict[str, Any]
) -> tuple[list[str], list[str]]:
    skills: list[str] = []
    domains: Counter[str] = Counter()
    for skill in normalized.get("normalized_skills", []):
        if not isinstance(skill, dict):
            continue
        name = skill.get("canonical_name") or skill.get("source_name")
        if isinstance(name, str) and name.strip() and name.strip() not in skills:
            skills.append(name.strip())
        for relation in skill.get("classifications", []):
            if isinstance(relation, dict) and relation.get("facet") == "domain":
                code = relation.get("code")
                if isinstance(code, str) and code:
                    domains[code] += 1
    return skills[:24], [code for code, _ in domains.most_common(8)]


def _role_context(annotation: dict[str, Any], feature: dict[str, Any]) -> list[str]:
    if feature.get("structured_values", {}).get("role_kind") != "historical":
        return []
    object_id = feature.get("source_object_id")
    for entry in annotation.get("work_experience", []):
        if isinstance(entry, dict) and entry.get("entry_id") == object_id:
            return (
                _fact_texts(entry.get("responsibilities"))
                + _fact_texts(entry.get("achievements"))
            )[:8]
    return []


def _load_profiles(run_dirs: list[Path]) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for run_dir in run_dirs:
        final = run_dir / "final"
        annotations = _read_json(final / "annotations_nested.json")
        normalized = _read_json(final / "normalized_annotations.json")
        features = [
            json.loads(line)
            for line in (final / "match_features.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        if not isinstance(annotations, list) or not isinstance(normalized, list):
            raise ValueError(f"CV run outputs must be lists: {run_dir}")
        annotation_by_id = {str(row["document_id"]): row for row in annotations}
        normalized_by_id = {str(row["document_id"]): row for row in normalized}
        if set(annotation_by_id) != set(normalized_by_id):
            raise ValueError(f"CV annotation and normalization sets differ: {run_dir}")
        for feature in features:
            if feature.get("feature_type") != "role":
                continue
            feature_id = str(feature["feature_id"])
            role_id = f"{run_dir.name}:{feature_id}"
            if role_id in seen:
                raise ValueError(f"duplicate CV role identity: {role_id}")
            seen.add(role_id)
            document_id = str(feature["document_id"])
            annotation = annotation_by_id[document_id]
            skills, domains = _document_context(
                annotation, normalized_by_id[document_id]
            )
            profiles.append(
                {
                    "document_id": role_id,
                    "cv_document_id": document_id,
                    "title": feature["raw_text"],
                    "role_kind": feature.get("structured_values", {}).get(
                        "role_kind"
                    ),
                    "responsibilities": _role_context(annotation, feature),
                    "skills": skills,
                    "skill_domains": domains,
                    "available_evidence_refs": [
                        str(item.get("source_id"))
                        for item in feature.get("evidence_refs", [])
                        if isinstance(item, dict) and item.get("source_id")
                    ],
                }
            )
    if not profiles:
        raise ValueError("selected CV runs contain no role features")
    return profiles


def classify(args: argparse.Namespace) -> None:
    _load_api_key(args.env_file)
    catalog = _validate_catalog(_read_json(args.catalog))
    profiles = _load_profiles(args.run)
    profile_by_id = {item["document_id"]: item for item in profiles}
    decisions = _checkpoint(args.checkpoint)
    if set(decisions) - set(profile_by_id):
        raise ValueError("checkpoint contains CV roles outside the selected runs")
    pending = [item for item in profiles if item["document_id"] not in decisions]
    batches = [
        pending[index : index + args.batch_size]
        for index in range(0, len(pending), args.batch_size)
    ]
    if batches:
        with ThreadPoolExecutor(
            max_workers=min(args.max_workers, len(batches)),
            thread_name_prefix="cv-position-taxonomy",
        ) as executor:
            futures = {
                executor.submit(
                    _request_validated_batch,
                    batch,
                    catalog,
                    args.model,
                    args.max_attempts,
                ): batch
                for batch in batches
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                decisions.update(future.result())
                _write_json_atomic(
                    args.checkpoint,
                    {
                        "schema": "cv-position-reclassification-checkpoint.v3",
                        "catalog_version": catalog["catalog_version"],
                        "model": args.model,
                        "decisions": decisions,
                    },
                )
                print(
                    json.dumps(
                        {
                            "completed_batches": completed,
                            "total_batches": len(batches),
                            "classified_roles": len(decisions),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    if set(decisions) != set(profile_by_id):
        raise ValueError("classification did not cover every selected CV role")


def _apply_decision(
    feature: dict[str, Any], decision: dict[str, Any], catalog: dict[str, Any]
) -> None:
    position = (
        catalog["_position_by_code"][decision["position_code"]]
        if decision["position_code"] is not None
        else None
    )
    family = (
        catalog["_family_by_code"][position["family_code"]]
        if position is not None
        else None
    )
    feature["canonical_id"] = None
    feature["canonical_name"] = position["name"] if position else None
    feature["taxonomy_version"] = catalog["catalog_version"]
    feature["resolution_status"] = (
        "resolved"
        if decision["classification_status"] == "resolved"
        else "unresolved"
    )
    feature["structured_values"].update(
        {
            "classification_schema_version": "job-position-classification.v3",
            "position_code": position["code"] if position else None,
            "family_code": family["code"] if family else None,
            "family_name": family["name"] if family else None,
            "candidate_positions": decision["candidate_positions"],
            "career_level": decision["career_level"],
            "leadership_scope": decision["leadership_scope"],
            "technology_focus_codes": decision["technology_focus_codes"],
            "industry_context_codes": decision["industry_context_codes"],
            "observed_skill_domain_codes": decision["observed_skill_domain_codes"],
            "confidence": decision["confidence"],
            "classification_status": decision["classification_status"],
            "review_reason_codes": decision["review_reason_codes"],
            "evidence_refs": decision["evidence_refs"],
            "classification_policy_version": catalog["classification_policy_version"],
        }
    )


def apply(args: argparse.Namespace) -> None:
    catalog = _validate_catalog(_read_json(args.catalog))
    profiles = _load_profiles(args.run)
    expected = {item["document_id"] for item in profiles}
    decisions = _validate_decisions(
        list(_checkpoint(args.checkpoint).values()),
        expected,
        catalog,
        profiles_by_id={item["document_id"]: item for item in profiles},
    )
    counts: Counter[str] = Counter()
    review_count = 0
    applied_at = datetime.now(timezone.utc).isoformat()
    args.output_root.mkdir(parents=True, exist_ok=True)
    for source_run_dir in args.run:
        run_dir = args.output_root / f"{source_run_dir.name}_position_v3"
        if run_dir.exists():
            raise ValueError(f"v3 output run already exists: {run_dir}")
        shutil.copytree(source_run_dir, run_dir)
        final = run_dir / "final"
        feature_path = final / "match_features.jsonl"
        features = [
            json.loads(line)
            for line in feature_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for feature in features:
            if feature.get("feature_type") != "role":
                continue
            decision = decisions[
                f"{source_run_dir.name}:{feature['feature_id']}"
            ]
            _apply_decision(feature, decision, catalog)
            counts[decision["classification_status"]] += 1
            review_count += int(
                decision["classification_status"] in {"ambiguous", "catalog_gap"}
            )
        _write_jsonl_atomic(feature_path, features)
        profile_path = final / "match_feature_profiles.json"
        grouped = _read_json(profile_path)
        for profile in grouped:
            for feature in profile.get("features", []):
                if feature.get("feature_type") == "role":
                    _apply_decision(
                        feature,
                        decisions[
                            f"{source_run_dir.name}:{feature['feature_id']}"
                        ],
                        catalog,
                    )
        _write_json_atomic(profile_path, grouped)
        match_profiles = [
            CVMatchFeatureResult.model_validate(profile) for profile in grouped
        ]
        capability_results = [
            build_capability_verification(profile) for profile in match_profiles
        ]
        export_capability_verification_profiles(
            capability_results,
            str(final / "capability_verification_profiles.json"),
        )
        export_capability_profiles_jsonl(
            capability_results,
            str(final / "capability_profiles.jsonl"),
        )
        export_capability_evidence_links_jsonl(
            capability_results,
            str(final / "capability_evidence_links.jsonl"),
        )
        manifest_path = run_dir / "manifest.json"
        manifest = _read_json(manifest_path)
        role_features = [
            feature
            for profile in match_profiles
            for feature in profile.features
            if feature.feature_type == "role"
        ]
        manifest.update(
            {
                "position_taxonomy_version": catalog["catalog_version"],
                "position_reclassified_at": applied_at,
                "position_reclassification_model": args.model,
                "position_classified_role_count": len(role_features),
                "position_review_required_count": sum(
                    feature.structured_values.get("classification_status")
                    in {"ambiguous", "catalog_gap"}
                    for feature in role_features
                ),
                "resolved_match_feature_count": sum(
                    feature.resolution_status == "resolved"
                    for profile in match_profiles
                    for feature in profile.features
                ),
                "capability_verification_derivation_version": (
                    CAPABILITY_VERIFICATION_DERIVATION_VERSION
                ),
                "capability_verification_profile_count": len(
                    capability_results
                ),
                "capability_profile_count": sum(
                    len(result.profiles) for result in capability_results
                ),
                "capability_evidence_link_count": sum(
                    len(result.evidence_links) for result in capability_results
                ),
                "research_report_path": str(run_dir / "research_report.md"),
                "report_generator_version": REPORT_GENERATOR_VERSION,
            }
        )
        _write_json_atomic(manifest_path, manifest)
        generate_run_report(run_dir)
    report = {
        "schema": "cv-position-reclassification-report.v3",
        "catalog_version": catalog["catalog_version"],
        "model": args.model,
        "applied_at": applied_at,
        "role_count": len(expected),
        "classification_status_counts": dict(sorted(counts.items())),
        "review_required_count": review_count,
    }
    _write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Classify precomputed CV role features with position taxonomy v3."
    )
    result.add_argument("command", choices=("classify", "apply"))
    result.add_argument("--run", action="append", required=True, type=Path)
    result.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "output" / "runs_position_v3",
    )
    result.add_argument(
        "--catalog",
        type=Path,
        default=(
            PROJECT_ROOT
            / "resources"
            / "taxonomy"
            / "position"
            / "3.0"
            / "position_taxonomy_catalog.v3.json"
        ),
    )
    result.add_argument(
        "--env-file", type=Path, default=PROJECT_ROOT / ".env"
    )
    result.add_argument(
        "--checkpoint",
        type=Path,
        default=PROJECT_ROOT / "output" / "cv_position_reclassification_v3.checkpoint.json",
    )
    result.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "output" / "cv_position_reclassification_v3.report.json",
    )
    result.add_argument("--model", default="deepseek-v4-flash")
    result.add_argument("--batch-size", type=int, default=20)
    result.add_argument("--max-workers", type=int, default=2500)
    result.add_argument("--max-attempts", type=int, default=3)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.model != "deepseek-v4-flash":
        raise ValueError("CV position classification model must be deepseek-v4-flash")
    if (
        args.batch_size < 1
        or args.max_workers < 1
        or args.max_workers > 2500
        or args.max_attempts < 1
    ):
        raise ValueError("classification concurrency arguments are out of range")
    if args.command == "classify":
        classify(args)
    else:
        apply(args)


if __name__ == "__main__":
    main()
