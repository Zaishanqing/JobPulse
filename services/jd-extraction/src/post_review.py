from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from .audit import utc_now_iso
from .exporter import (
    export_jsonl,
    export_failed_cases,
    export_nested_json,
    export_review_flags,
    export_xlsx,
)
from .load_excel import load_excel_rows
from .models import JDExtractionResult
from .normalizer import load_normalization_map, normalize_extraction
from .preprocess import preprocess_row
from .prompt_builder import build_system_prompt
from .provenance import align_all_evidence
from .report_generator import generate_run_report
from .skill_taxonomy import (
    build_classification_records,
    iter_jd_skill_occurrences,
    load_skill_taxonomy_snapshot,
    validate_snapshot_against_normalization_map,
    write_unified_normalized_artifacts,
)
from .validator import (
    BUSINESS_VALIDATOR_VERSION,
    validate_business_rules,
    validate_normalized_rules,
    validate_semantic_constraints,
    validate_explicit_section_completeness,
    validate_skill_item_type_contract,
)


COLLECTION_ID_FIELDS = {
    "responsibilities": "requirement_id",
    "requirements": "requirement_id",
    "company_facts": "fact_id",
    "employment_facts": "fact_id",
}
EDITABLE_FIELDS = {
    "responsibilities": {"action", "modality"},
    "requirements": {"modality", "domain", "value"},
    "company_facts": {"kind", "value"},
    "employment_facts": {"kind", "value"},
}


def _target_item(annotation: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    collection = decision.get("collection")
    if collection not in COLLECTION_ID_FIELDS:
        raise ValueError(f"Unsupported post-review collection: {collection!r}")
    object_id = decision.get("object_id")
    id_field = COLLECTION_ID_FIELDS[collection]
    matches = [item for item in annotation[collection] if item.get(id_field) == object_id]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one {collection} object {object_id!r}; found {len(matches)}."
        )
    return matches[0]


def apply_annotation_decisions(
    annotation: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    revised = deepcopy(annotation)
    for decision in decisions:
        if decision.get("document_id") != revised.get("document_id"):
            raise ValueError("Decision document_id does not match the target annotation.")
        action = decision.get("action")
        if action == "replace_field":
            target = _target_item(revised, decision)
            collection = decision["collection"]
            field = decision.get("field")
            if field not in EDITABLE_FIELDS[collection]:
                raise ValueError(f"Field {field!r} is not editable in {collection}.")
            if target.get(field) != decision.get("expected_value"):
                raise ValueError(
                    f"Expected {collection}.{field}={decision.get('expected_value')!r}, "
                    f"found {target.get(field)!r}."
                )
            target[field] = decision["new_value"]
            continue
        if action == "remove_object":
            target = _target_item(revised, decision)
            collection = decision["collection"]
            expected = decision.get("expected_object")
            actual = {key: target.get(key) for key in expected}
            if actual != expected:
                raise ValueError(f"Expected object fields {expected!r}, found {actual!r}.")
            revised[collection].remove(target)
            continue
        if action == "replace_object":
            target = _target_item(revised, decision)
            collection = decision["collection"]
            expected = decision.get("expected_object")
            replacement = decision.get("new_object")
            if not isinstance(expected, dict) or not isinstance(replacement, dict):
                raise ValueError("replace_object requires expected_object and new_object mappings.")
            actual = {key: target.get(key) for key in expected}
            if actual != expected:
                raise ValueError(f"Expected object fields {expected!r}, found {actual!r}.")
            id_field = COLLECTION_ID_FIELDS[collection]
            if replacement.get(id_field) != target.get(id_field):
                raise ValueError(f"replace_object must preserve {id_field}.")
            target_index = revised[collection].index(target)
            revised[collection][target_index] = deepcopy(replacement)
            continue
        if action == "append_object":
            collection = decision.get("collection")
            if collection not in COLLECTION_ID_FIELDS:
                raise ValueError(f"Unsupported post-review collection: {collection!r}")
            expected_size = decision.get("expected_collection_size")
            if not isinstance(expected_size, int) or len(revised[collection]) != expected_size:
                raise ValueError(
                    f"Expected {collection} size {expected_size!r}, found {len(revised[collection])}."
                )
            new_object = decision.get("new_object")
            if not isinstance(new_object, dict):
                raise ValueError("append_object requires a new_object mapping.")
            id_field = COLLECTION_ID_FIELDS[collection]
            object_id = new_object.get(id_field)
            if not isinstance(object_id, str) or not object_id:
                raise ValueError(f"append_object requires a non-empty {id_field}.")
            if any(item.get(id_field) == object_id for item in revised[collection]):
                raise ValueError(f"Cannot append duplicate {collection} object {object_id!r}.")
            revised[collection].append(deepcopy(new_object))
            continue
        if action == "replace_skill_item_types":
            replacements = decision.get("items")
            if not isinstance(replacements, list) or not replacements:
                raise ValueError("replace_skill_item_types requires a non-empty items list.")
            for replacement in replacements:
                requirement_id = replacement.get("requirement_id")
                requirements = [
                    item for item in revised["requirements"]
                    if item.get("requirement_id") == requirement_id and item.get("kind") == "skill"
                ]
                if len(requirements) != 1:
                    raise ValueError(f"Expected one skill requirement {requirement_id!r}.")
                name = replacement.get("name")
                skill_items = [item for item in requirements[0]["items"] if item.get("name") == name]
                if len(skill_items) != 1:
                    raise ValueError(
                        f"Expected one skill item {name!r} in requirement {requirement_id!r}."
                    )
                target_item = skill_items[0]
                expected_type = replacement.get("expected_item_type")
                if target_item.get("item_type") != expected_type:
                    raise ValueError(
                        f"Expected skill {name!r} item_type={expected_type!r}, "
                        f"found {target_item.get('item_type')!r}."
                    )
                target_item["item_type"] = replacement["new_item_type"]
            continue
        raise ValueError(f"Unsupported post-review action: {action!r}")
    return revised


def apply_post_review_file(
    decision_path: str | Path,
    output_dir: str | Path,
    normalization_path: str | Path,
    selected_run_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    source_path = Path(decision_path).resolve()
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if payload.get("version") != "1.0":
        raise ValueError("Post-review decision file version must be 1.0.")
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("Post-review decision file must contain a non-empty runs list.")

    output_root = Path(output_dir)
    normalization_file = Path(normalization_path).resolve()
    normalization_map = load_normalization_map(str(normalization_file))
    receipts: list[dict[str, Any]] = []
    for run_decision in runs:
        run_id = run_decision.get("run_id")
        if selected_run_ids is not None and run_id not in selected_run_ids:
            continue
        decisions = run_decision.get("decisions")
        skill_type_corrections = run_decision.get("skill_type_corrections", [])
        recoveries = run_decision.get("recoveries", [])
        if not isinstance(run_id, str) or not isinstance(decisions, list):
            raise ValueError("Each run decision requires run_id and a decisions list.")
        if not isinstance(skill_type_corrections, list):
            raise ValueError("skill_type_corrections must be a list.")
        if not isinstance(recoveries, list) or not (decisions or skill_type_corrections or recoveries):
            raise ValueError("Each run decision requires at least one correction or recovery.")
        run_dir = output_root / "runs" / run_id
        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("run_id") != run_id:
            raise ValueError(f"Manifest run_id mismatch for {run_id}.")
        rows = load_excel_rows(str(Path(manifest["input_path"])))
        record_paths = sorted((run_dir / "records" / "success").glob("*.json"))
        records = [json.loads(path.read_text(encoding="utf-8")) for path in record_paths]
        by_document = {record["annotation"]["document_id"]: record for record in records}
        if len(by_document) != len(records):
            raise ValueError(f"Duplicate document_id in successful records for {run_id}.")

        decisions_by_document: dict[str, list[dict[str, Any]]] = {}
        for decision in decisions:
            decisions_by_document.setdefault(decision.get("document_id"), []).append(decision)
        skill_corrections_by_document: dict[str, list[dict[str, Any]]] = {}
        for correction in skill_type_corrections:
            document_id = correction.get("document_id")
            item = {key: value for key, value in correction.items() if key != "document_id"}
            skill_corrections_by_document.setdefault(document_id, []).append(item)
        for document_id, items in skill_corrections_by_document.items():
            decisions_by_document.setdefault(document_id, []).append({
                "document_id": document_id,
                "action": "replace_skill_item_types",
                "items": items,
            })
        recovery_ids = {item.get("document_id") for item in recoveries}
        if None in recovery_ids or len(recovery_ids) != len(recoveries):
            raise ValueError(f"Recovery document_id values must be unique in {run_id}.")
        if recovery_ids & set(by_document):
            raise ValueError(f"Recovery document already exists as success in {run_id}.")
        failed_cases_path = run_dir / "final" / "failed_cases.jsonl"
        failed_cases = [
            json.loads(line)
            for line in failed_cases_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        failed_record_paths: list[Path] = []
        for recovery in recoveries:
            document_id = recovery["document_id"]
            row_index = int(recovery["row_index"])
            audit_path = run_dir / str(recovery["audit_path"])
            audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
            if audit_payload.get("jd_id") != document_id or int(audit_payload.get("row_index")) != row_index:
                raise ValueError(f"Recovery audit identity mismatch for {document_id} in {run_id}.")
            annotation_field = recovery.get("annotation_field", "canonicalized_json")
            raw_annotation = audit_payload.get(annotation_field)
            if not isinstance(raw_annotation, dict) or raw_annotation.get("document_id") != document_id:
                raise ValueError(f"Recovery annotation is missing for {document_id} in {run_id}.")
            recovery_decisions = recovery.get("decisions", [])
            if not isinstance(recovery_decisions, list):
                raise ValueError(f"Recovery decisions must be a list for {document_id}.")
            recovered_annotation = apply_annotation_decisions(raw_annotation, recovery_decisions)
            record = {
                "run_id": run_id,
                "jd_id": document_id,
                "row_index": row_index,
                "status": "success",
                "annotation": recovered_annotation,
                "review_flags": [],
            }
            record_path = run_dir / "records" / "success" / f"{row_index:06d}_{document_id}.json"
            record_paths.append(record_path)
            records.append(record)
            by_document[document_id] = record
            failed_matches = [
                item for item in failed_cases
                if item.get("jd_id") == document_id and int(item.get("row_index")) == row_index
            ]
            if len(failed_matches) != 1:
                raise ValueError(f"Expected one failed case for recovery {document_id} in {run_id}.")
            failed_cases.remove(failed_matches[0])
            failed_record_matches = list((run_dir / "records" / "failed").glob(f"{row_index:06d}_{document_id}.json"))
            if len(failed_record_matches) != 1:
                raise ValueError(f"Expected one failed record for recovery {document_id} in {run_id}.")
            failed_record_paths.append(failed_record_matches[0])
        paired_records = sorted(zip(record_paths, records, strict=True), key=lambda item: int(item[1]["row_index"]))
        record_paths = [item[0] for item in paired_records]
        records = [item[1] for item in paired_records]

        unknown_documents = set(decisions_by_document) - set(by_document)
        if unknown_documents:
            raise ValueError(f"Unknown decision document_id values in {run_id}: {sorted(unknown_documents)}")

        annotations: list[JDExtractionResult] = []
        normalized_results = []
        all_review_flags: list[dict[str, Any]] = []
        for record in records:
            row_index = int(record["row_index"])
            jd_input, failure = preprocess_row(rows[row_index - 1], row_index)
            if failure is not None or jd_input is None:
                raise ValueError(f"Cannot reconstruct source row {row_index} in {run_id}.")
            raw_annotation = record["annotation"]
            document_id = raw_annotation["document_id"]
            revised = apply_annotation_decisions(
                raw_annotation,
                decisions_by_document.get(document_id, []),
            )
            annotation = JDExtractionResult.model_validate(revised)
            annotation = align_all_evidence(annotation, jd_input["source_blocks"])
            validate_semantic_constraints(annotation)
            validate_explicit_section_completeness(annotation, jd_input["source_blocks"])
            validate_skill_item_type_contract(annotation, normalization_map)
            normalized = normalize_extraction(annotation, normalization_map, jd_input["jd_text"])
            review_flags = [
                *validate_business_rules(annotation),
                *validate_normalized_rules(normalized),
            ]
            record["annotation"] = annotation.model_dump(exclude_none=True)
            record["review_flags"] = review_flags
            annotations.append(annotation)
            normalized_results.append(normalized)
            all_review_flags.extend(review_flags)

        final_dir = run_dir / "final"
        for path, record in zip(record_paths, records, strict=True):
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in failed_record_paths:
            path.unlink()
        export_jsonl(annotations, str(final_dir / "annotations.jsonl"))
        export_nested_json(annotations, str(final_dir / "annotations_nested.json"))
        taxonomy_path = Path(manifest["skill_taxonomy_snapshot_path"])
        skill_taxonomy = load_skill_taxonomy_snapshot(taxonomy_path)
        validate_snapshot_against_normalization_map(
            skill_taxonomy, normalization_map
        )
        classification_records, classification_summary = build_classification_records(
            iter_jd_skill_occurrences(normalized_results), skill_taxonomy
        )
        unified_normalized_results = write_unified_normalized_artifacts(
            normalized_results,
            classification_records,
            skill_taxonomy,
            final_dir,
        )
        export_review_flags(all_review_flags, str(final_dir / "review_flags.jsonl"))
        export_failed_cases(failed_cases, str(final_dir / "failed_cases.jsonl"))
        export_xlsx(
            annotations,
            unified_normalized_results,
            all_review_flags,
            str(final_dir / "annotations.xlsx"),
        )

        applied_at = utc_now_iso()
        application_receipt = {
            "version": "1.0",
            "run_id": run_id,
            "applied_at": applied_at,
            "source_decision_path": str(source_path.resolve()),
            "decisions": decisions,
            "skill_type_corrections": skill_type_corrections,
            "recoveries": recoveries,
        }
        receipt_path = run_dir / "post_review_decisions.json"
        applications: list[dict[str, Any]] = []
        if receipt_path.exists():
            existing_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if existing_receipt.get("version") == "2.0":
                applications = existing_receipt.get("applications", [])
                if not isinstance(applications, list):
                    raise ValueError(f"Invalid post-review ledger: {receipt_path}")
            elif existing_receipt.get("version") == "1.0":
                applications = [existing_receipt]
            else:
                raise ValueError(f"Unsupported post-review receipt version: {receipt_path}")
        applications.append(application_receipt)
        run_receipt = {"version": "2.0", "run_id": run_id, "applications": applications}
        receipt_path.write_text(json.dumps(run_receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        cumulative_decision_count = sum(
            len(item.get("decisions", [])) + len(item.get("skill_type_corrections", []))
            for item in applications
        )
        cumulative_recovery_count = sum(len(item.get("recoveries", [])) for item in applications)
        manifest.update({
            "business_validator_version": BUSINESS_VALIDATOR_VERSION,
            "normalization_path": str(normalization_file),
            "normalization_config_version": str(normalization_map["version"]),
            "review_flag_count": len(all_review_flags),
            "success_count": len(annotations),
            "failed_count": len(failed_cases),
            "post_review_applied_at": applied_at,
            "post_review_decision_path": str(receipt_path.resolve()),
            "post_review_applied_decision_count": cumulative_decision_count,
            "post_review_acknowledged_flag_count": 0,
            "post_review_correction_count": cumulative_decision_count,
            "post_review_recovery_count": cumulative_recovery_count,
            "post_review_prompt_version": "jd-prompt.v1",
            **classification_summary,
        })
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        with (run_dir / "logs.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "timestamp": applied_at,
                "run_id": run_id,
                "event_type": "post_review_applied",
                "decision_count": len(decisions) + len(skill_type_corrections),
                "review_flag_count": len(all_review_flags),
                "business_validator_version": BUSINESS_VALIDATOR_VERSION,
            }, ensure_ascii=False) + "\n")
        generate_run_report(run_dir)
        receipts.append({
            "run_id": run_id,
            "decisions": len(decisions) + len(skill_type_corrections),
            "review_flags": len(all_review_flags),
            "documents": len(annotations),
        })
    if selected_run_ids is not None:
        missing_run_ids = selected_run_ids - {item["run_id"] for item in receipts}
        if missing_run_ids:
            raise ValueError(f"Selected run_id values were not found in decision file: {sorted(missing_run_ids)}")
    return receipts
