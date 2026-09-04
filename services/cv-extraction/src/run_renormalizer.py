from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .audit import utc_now_iso
from .capability_verification import (
    CAPABILITY_VERIFICATION_DERIVATION_VERSION,
    build_capability_verification,
)
from .deduplicator import deduplicate_extraction
from .deterministic_fields import canonicalize_authoritative_fields
from .exporter import (
    export_capability_evidence_links_jsonl,
    export_capability_profiles_jsonl,
    export_capability_verification_profiles,
    export_match_feature_profiles,
    export_match_features_jsonl,
    export_review_flags,
    export_jsonl,
    export_nested_json,
    export_xlsx,
)
from .match_features import MATCH_FEATURE_DERIVATION_VERSION, build_cv_match_features
from .models import CVExtractionResult
from .normalizer import load_normalization_map, normalize_extraction
from .report_generator import REPORT_GENERATOR_VERSION, generate_run_report
from .skill_taxonomy import (
    build_classification_records,
    iter_cv_skill_occurrences,
    load_skill_taxonomy_snapshot,
    taxonomy_snapshot_version,
    validate_snapshot_against_normalization_map,
    write_unified_normalized_artifacts,
)
from .validator import (
    validate_business_rules,
    validate_normalized_rules,
    validate_semantic_constraints,
    validate_skill_item_type_contract,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Required run artifact does not exist: {path}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


DEFAULT_SKILL_TAXONOMY_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "taxonomy"
    / "2.0"
    / "skill_taxonomy_snapshot.json"
)


def renormalize_run(
    run_dir: str | Path,
    normalization_path: str | Path,
    *,
    as_of_date: date,
    skill_taxonomy_path: str | Path = DEFAULT_SKILL_TAXONOMY_PATH,
) -> dict[str, int | str]:
    run_path = Path(run_dir)
    manifest_path = run_path / "manifest.json"
    final_dir = run_path / "final"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Run manifest does not exist: {manifest_path}")
    normalization_file = Path(normalization_path).resolve()
    normalization_map = load_normalization_map(str(normalization_file))
    taxonomy_file = Path(skill_taxonomy_path).resolve()
    skill_taxonomy = load_skill_taxonomy_snapshot(taxonomy_file)
    validate_snapshot_against_normalization_map(skill_taxonomy, normalization_map)
    canonicalization_corrections: list[dict[str, Any]] = []
    annotations = []
    for payload in _read_jsonl(final_dir / "annotations.jsonl"):
        canonicalized, corrections = canonicalize_authoritative_fields(
            payload, normalization_map
        )
        canonicalization_corrections.extend(
            {"document_id": canonicalized.get("document_id"), **correction}
            for correction in corrections
        )
        annotations.append(CVExtractionResult.model_validate(canonicalized))

    normalized_results = []
    match_profiles = []
    review_flags: list[dict[str, Any]] = []
    for annotation_index, annotation in enumerate(annotations):
        annotation = deduplicate_extraction(annotation)
        annotations[annotation_index] = annotation
        validate_semantic_constraints(annotation)
        validate_skill_item_type_contract(annotation, normalization_map)
        normalized = normalize_extraction(annotation, normalization_map)
        normalized_results.append(normalized)
        review_flags.extend(validate_business_rules(annotation))
        review_flags.extend(validate_normalized_rules(normalized))
        match_profiles.append(
            build_cv_match_features(
                annotation,
                normalized,
                normalization_map,
                as_of_date=as_of_date,
            )
        )

    export_jsonl(annotations, str(final_dir / "annotations.jsonl"))
    export_nested_json(annotations, str(final_dir / "annotations_nested.json"))

    classification_records, classification_summary = build_classification_records(
        iter_cv_skill_occurrences(normalized_results), skill_taxonomy
    )
    unified_normalized_results = write_unified_normalized_artifacts(
        normalized_results, classification_records, skill_taxonomy, final_dir
    )
    export_match_features_jsonl(match_profiles, str(final_dir / "match_features.jsonl"))
    export_match_feature_profiles(
        match_profiles, str(final_dir / "match_feature_profiles.json")
    )
    capability_verification_profiles = [
        build_capability_verification(profile) for profile in match_profiles
    ]
    export_capability_verification_profiles(
        capability_verification_profiles,
        str(final_dir / "capability_verification_profiles.json"),
    )
    export_capability_profiles_jsonl(
        capability_verification_profiles,
        str(final_dir / "capability_profiles.jsonl"),
    )
    export_capability_evidence_links_jsonl(
        capability_verification_profiles,
        str(final_dir / "capability_evidence_links.jsonl"),
    )
    export_review_flags(review_flags, str(final_dir / "review_flags.jsonl"))
    export_xlsx(
        annotations,
        unified_normalized_results,
        review_flags,
        str(final_dir / "annotations.xlsx"),
    )

    feature_count = sum(len(profile.features) for profile in match_profiles)
    resolved_feature_count = sum(
        feature.resolution_status == "resolved"
        for profile in match_profiles
        for feature in profile.features
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("skill_type_corrections_path", None)
    manifest.pop("skill_type_corrections_hash", None)
    manifest.pop("skill_type_alignment_count", None)
    manifest.pop("skill_type_alignment_policy", None)
    manifest.update(
        {
            "normalization_path": str(normalization_file),
            "normalization_taxonomy_version": str(normalization_map["version"]),
            "normalization_config_version": str(normalization_map["version"]),
            "skill_taxonomy_snapshot_path": str(taxonomy_file),
            "skill_taxonomy_snapshot_version": taxonomy_snapshot_version(
                skill_taxonomy
            ),
            "renormalized_at": utc_now_iso(),
            "match_feature_as_of_date": as_of_date.isoformat(),
            "match_feature_derivation_version": MATCH_FEATURE_DERIVATION_VERSION,
            "match_feature_profile_count": len(match_profiles),
            "match_feature_count": feature_count,
            "resolved_match_feature_count": resolved_feature_count,
            "capability_verification_derivation_version": (
                CAPABILITY_VERIFICATION_DERIVATION_VERSION
            ),
            "capability_verification_profile_count": len(
                capability_verification_profiles
            ),
            "capability_profile_count": sum(
                len(result.profiles) for result in capability_verification_profiles
            ),
            "capability_evidence_link_count": sum(
                len(result.evidence_links)
                for result in capability_verification_profiles
            ),
            "review_flag_count": len(review_flags),
            "canonicalization_correction_count": len(canonicalization_corrections),
            **classification_summary,
            "research_report_path": str(run_path / "research_report.md"),
            "report_generator_version": REPORT_GENERATOR_VERSION,
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (run_path / "logs.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "timestamp": utc_now_iso(),
                    "run_id": manifest.get("run_id"),
                    "event_type": "run_renormalized_and_match_features_built",
                    "normalization_path": str(normalization_file),
                    "as_of_date": as_of_date.isoformat(),
                    "annotation_count": len(annotations),
                    "match_feature_count": feature_count,
                    "review_flag_count": len(review_flags),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    generate_run_report(run_path)
    return {
        "annotation_count": len(annotations),
        "normalized_skill_count": sum(
            len(result.normalized_skills) for result in normalized_results
        ),
        "unresolved_skill_count": sum(
            len(result.unresolved_items) for result in normalized_results
        ),
        "match_feature_count": feature_count,
        "resolved_match_feature_count": resolved_feature_count,
        "capability_profile_count": sum(
            len(result.profiles) for result in capability_verification_profiles
        ),
        "capability_evidence_link_count": sum(
            len(result.evidence_links) for result in capability_verification_profiles
        ),
        "review_flag_count": len(review_flags),
        "as_of_date": as_of_date.isoformat(),
        **classification_summary,
    }
