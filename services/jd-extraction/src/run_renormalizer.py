from __future__ import annotations

import json
from pathlib import Path

from .audit import utc_now_iso
from .config_iteration import load_decision_ledger, lookup_semantic_decision
from .deterministic_fields import (
    canonicalize_authoritative_fields,
    populate_deterministic_fields,
)
from .exporter import (
    export_jsonl,
    export_nested_json,
    export_review_flags,
    export_xlsx,
)
from .load_excel import load_excel_rows
from .models import JDExtractionResult
from .normalizer import load_normalization_map, normalize_extraction
from .preprocess import preprocess_row
from .provenance import align_all_evidence
from .skill_taxonomy import (
    build_classification_records,
    iter_jd_skill_occurrences,
    load_skill_taxonomy_snapshot,
    taxonomy_snapshot_version,
    validate_snapshot_against_normalization_map,
    write_unified_normalized_artifacts,
)
from .validator import (
    validate_business_rules,
    validate_normalized_rules,
    validate_skill_item_type_contract,
)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


DEFAULT_SKILL_TAXONOMY_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "skill_taxonomy_snapshot.json"
)
DEFAULT_DECISION_LEDGER_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "normalization_decision_ledger.json"
)


def renormalize_run(
    run_dir: str | Path,
    normalization_path: str,
    skill_taxonomy_path: str | Path = DEFAULT_SKILL_TAXONOMY_PATH,
    decision_ledger_path: str | Path = DEFAULT_DECISION_LEDGER_PATH,
) -> dict[str, int]:
    run_path = Path(run_dir)
    manifest_path = run_path / "manifest.json"
    final_dir = run_path / "final"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    input_path = Path(manifest["input_path"])
    rows = load_excel_rows(str(input_path))
    normalization_map = load_normalization_map(normalization_path)
    decision_ledger = load_decision_ledger(decision_ledger_path)
    taxonomy_path = Path(skill_taxonomy_path).resolve()
    skill_taxonomy = load_skill_taxonomy_snapshot(taxonomy_path)
    validate_snapshot_against_normalization_map(skill_taxonomy, normalization_map)

    record_entries = []
    for record_path in (run_path / "records" / "success").glob("*.json"):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record_entries.append((record_path, record))
    record_entries.sort(key=lambda item: int(item[1]["row_index"]))
    records = [record for _, record in record_entries]
    if len(records) != int(manifest["success_count"]):
        raise ValueError("Successful record count does not match the run manifest.")

    annotations: list[JDExtractionResult] = []
    normalized_results = []
    all_review_flags: list[dict] = []
    for record_path, record in record_entries:
        row_index = int(record["row_index"])
        saved_document_id = record["annotation"]["document_id"]
        jd_input, failure = preprocess_row(
            rows[row_index - 1],
            row_index,
            document_id=saved_document_id,
        )
        if failure is not None or jd_input is None:
            raise ValueError(f"Cannot reconstruct source row {row_index} for renormalization.")
        canonical_annotation, corrections = canonicalize_authoritative_fields(
            record["annotation"],
            normalization_map,
            jd_input["source_blocks"],
        )
        deterministic_annotation = populate_deterministic_fields(
            canonical_annotation, saved_document_id
        )
        extraction = JDExtractionResult.model_validate(deterministic_annotation)
        extraction = align_all_evidence(extraction, jd_input["source_blocks"])
        validate_skill_item_type_contract(extraction, normalization_map)
        annotations.append(extraction)
        normalized = normalize_extraction(extraction, normalization_map, jd_input["jd_text"])
        normalized_results.append(normalized)
        review_flags = [
            *validate_business_rules(extraction),
            *validate_normalized_rules(normalized),
        ]
        record["review_flags"] = review_flags
        record["annotation"] = extraction.model_dump(exclude_none=True)
        record["normalized"] = normalized.model_dump(exclude_none=True)
        if corrections:
            record.setdefault("deterministic_corrections", []).extend(corrections)
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        all_review_flags.extend(review_flags)

    classification_records, classification_summary = build_classification_records(
        iter_jd_skill_occurrences(normalized_results), skill_taxonomy
    )
    unified_normalized_results = write_unified_normalized_artifacts(
        normalized_results, classification_records, skill_taxonomy, final_dir
    )
    export_jsonl(annotations, str(final_dir / "annotations.jsonl"))
    export_nested_json(annotations, str(final_dir / "annotations_nested.json"))
    export_review_flags(all_review_flags, str(final_dir / "review_flags.jsonl"))
    export_xlsx(
        annotations,
        unified_normalized_results,
        all_review_flags,
        str(final_dir / "annotations.xlsx"),
    )

    config_path = Path(normalization_path).resolve()
    manifest["normalization_path"] = str(config_path)
    manifest["normalization_config_version"] = str(normalization_map["version"])
    manifest["skill_taxonomy_snapshot_path"] = str(taxonomy_path)
    manifest["skill_taxonomy_snapshot_version"] = taxonomy_snapshot_version(skill_taxonomy)
    manifest.update(classification_summary)
    manifest["review_flag_count"] = len(all_review_flags)
    manifest["renormalized_at"] = utc_now_iso()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with (run_path / "logs.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "timestamp": manifest["renormalized_at"],
            "run_id": manifest["run_id"],
            "event_type": "run_renormalized",
            "normalization_config_version": manifest["normalization_config_version"],
            "normalized_count": len(normalized_results),
        }, ensure_ascii=False) + "\n")

    resolved = sum(
        skill.resolution_status == "resolved"
        for result in normalized_results
        for requirement in result.normalized_requirements
        for skill in requirement.skills
    )
    total = sum(
        len(requirement.skills)
        for result in normalized_results
        for requirement in result.normalized_requirements
    )
    disposition_counts = {
        "resolved_identity": resolved,
        "generic_unresolved": 0,
        "ambiguous": 0,
        "excluded_non_skill": 0,
        "pending_review": 0,
        "unreviewed_unresolved": 0,
    }
    for result in normalized_results:
        for requirement in result.normalized_requirements:
            for skill in requirement.skills:
                if skill.resolution_status == "resolved":
                    continue
                decision = lookup_semantic_decision(
                    decision_ledger,
                    skill.source_name,
                    skill.category_code,
                )
                action = decision.get("action") if decision is not None else None
                bucket = (
                    action
                    if action
                    in {
                        "generic_unresolved",
                        "ambiguous",
                        "excluded_non_skill",
                        "pending_review",
                    }
                    else "unreviewed_unresolved"
                )
                disposition_counts[bucket] += 1
    resolution_summary = {
        "version": 1,
        "run_id": manifest["run_id"],
        "total_skill_occurrences": total,
        "identity_resolution_rate": round(resolved / total, 6) if total else 0.0,
        "counts": disposition_counts,
        "decision_ledger_path": str(Path(decision_ledger_path).resolve()),
    }
    (final_dir / "normalization_resolution_summary.json").write_text(
        json.dumps(resolution_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "documents": len(normalized_results),
        "resolved_skills": resolved,
        "total_skills": total,
        **classification_summary,
    }
