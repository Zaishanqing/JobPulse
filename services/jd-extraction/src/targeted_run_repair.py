from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .audit import utc_now_iso
from .deduplicator import deduplicate_extraction
from .exporter import (
    export_jsonl,
    export_nested_json,
    export_review_flags,
    export_xlsx,
)
from .load_excel import load_excel_rows
from .models import JDExtractionResult
from .normalizer import load_normalization_map, normalize_extraction
from .pipeline import JDExtractionPipeline
from .preprocess import preprocess_row
from .prompt_builder import build_user_prompt
from .provenance import align_all_evidence
from .run_identity_migration import _resolve_input_path
from .skill_taxonomy import (
    build_classification_records,
    iter_jd_skill_occurrences,
    load_skill_taxonomy_snapshot,
    validate_snapshot_against_normalization_map,
    write_unified_normalized_artifacts,
)
from .validator import (
    validate_business_rules,
    validate_explicit_section_completeness,
    validate_normalized_rules,
    validate_semantic_constraints,
    validate_skill_item_type_contract,
)


COLLECTION_ID_FIELDS = {
    "responsibilities": "requirement_id",
    "requirements": "requirement_id",
    "company_facts": "fact_id",
    "employment_facts": "fact_id",
}


def redact_extraction_attempts(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    redacted = []
    for attempt in attempts:
        item = {key: deepcopy(value) for key, value in attempt.items() if key != "raw_response"}
        raw_response = attempt.get("raw_response")
        if isinstance(raw_response, str):
            item["raw_response_present"] = True
        redacted.append(item)
    return redacted


def _target_object(annotation: dict[str, Any], collection: str, object_id: str) -> dict[str, Any]:
    id_field = COLLECTION_ID_FIELDS.get(collection)
    if id_field is None:
        raise ValueError(f"Unsupported collection: {collection!r}")
    matches = [item for item in annotation.get(collection, []) if item.get(id_field) == object_id]
    if len(matches) != 1:
        raise ValueError(f"Expected one {collection} object {object_id!r}; found {len(matches)}.")
    return matches[0]


def _replace_field_path(target: Any, field_path: str, expected: Any, replacement: Any) -> None:
    parts = field_path.replace("]", "").replace("[", ".").split(".")
    current = target
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    final = parts[-1]
    actual = current[int(final)] if isinstance(current, list) else current.get(final)
    if actual != expected:
        raise ValueError(f"Expected {field_path}={expected!r}; found {actual!r}.")
    if isinstance(current, list):
        current[int(final)] = replacement
    else:
        current[final] = replacement


def _field_path_value(target: Any, field_path: str) -> Any:
    current = target
    for part in field_path.replace("]", "").replace("[", ".").split("."):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def apply_replacement(annotation: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    revised = deepcopy(annotation)
    if decision.get("document_id") != revised.get("document_id"):
        raise ValueError("Decision document_id does not match annotation.")
    target = _target_object(revised, decision["collection"], decision["object_id"])
    expected_value = decision.get("expected_value")
    new_value = decision.get("new_value")
    if decision.get("operation") == "remove_inserted_boss":
        current_value = _field_path_value(target, decision["field_path"])
        if not isinstance(current_value, str) or re.search(r"boss", current_value, re.IGNORECASE) is None:
            raise ValueError("Selected BOSS repair target does not contain the expected artifact.")
        expected_value = current_value
        new_value = re.sub(r"boss", "", current_value, flags=re.IGNORECASE)
    _replace_field_path(
        target,
        decision["field_path"],
        expected_value,
        new_value,
    )
    return revised


def _run_context(run_path: Path, data_root: str | Path) -> tuple[dict[str, Any], Path, list[dict]]:
    manifest = json.loads((run_path / "manifest.json").read_text(encoding="utf-8"))
    input_path = _resolve_input_path(str(manifest["input_path"]), Path(data_root))
    return manifest, input_path, load_excel_rows(str(input_path))


def _record_entries(run_path: Path) -> list[tuple[Path, dict[str, Any]]]:
    entries = [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in (run_path / "records" / "success").glob("*.json")
    ]
    return sorted(entries, key=lambda item: int(item[1]["row_index"]))


def _rebuild_aggregates(
    run_path: Path,
    rows: list[dict],
    normalization_path: str,
) -> dict[str, int]:
    normalization_map = load_normalization_map(normalization_path)
    annotations = []
    normalized_results = []
    review_flags = []
    for _, record in _record_entries(run_path):
        annotation = JDExtractionResult.model_validate(record["annotation"])
        jd_input, failure = preprocess_row(
            rows[int(record["row_index"]) - 1],
            int(record["row_index"]),
            document_id=annotation.document_id,
        )
        if failure is not None or jd_input is None:
            raise ValueError(f"Cannot reconstruct row {record['row_index']} in {run_path.name}.")
        annotations.append(annotation)
        normalized_results.append(
            normalize_extraction(annotation, normalization_map, jd_input["jd_text"])
        )
        review_flags.extend(record.get("review_flags", []))

    final_dir = run_path / "final"
    manifest = json.loads((run_path / "manifest.json").read_text(encoding="utf-8"))
    skill_taxonomy = load_skill_taxonomy_snapshot(
        manifest["skill_taxonomy_snapshot_path"]
    )
    validate_snapshot_against_normalization_map(
        skill_taxonomy, normalization_map
    )
    classification_records, classification_summary = build_classification_records(
        iter_jd_skill_occurrences(normalized_results), skill_taxonomy
    )
    export_jsonl(annotations, str(final_dir / "annotations.jsonl"))
    export_nested_json(annotations, str(final_dir / "annotations_nested.json"))
    unified_normalized_results = write_unified_normalized_artifacts(
        normalized_results,
        classification_records,
        skill_taxonomy,
        final_dir,
    )
    export_review_flags(review_flags, str(final_dir / "review_flags.jsonl"))
    export_xlsx(
        annotations,
        unified_normalized_results,
        review_flags,
        str(final_dir / "annotations.xlsx"),
    )
    return {
        "documents": len(annotations),
        "review_flags": len(review_flags),
        **classification_summary,
    }


def apply_targeted_replacements(
    decision_path: str | Path,
    output_dir: str | Path,
    data_root: str | Path,
    normalization_path: str,
) -> list[dict[str, Any]]:
    source_path = Path(decision_path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if payload.get("version") != "1.0" or not isinstance(payload.get("runs"), list):
        raise ValueError("Targeted replacement file must use version 1.0 and contain runs.")

    # Validate every selected edit before the first output file is changed.
    for run_payload in payload["runs"]:
        run_path = Path(output_dir) / "runs" / run_payload["run_id"]
        _, _, rows = _run_context(run_path, data_root)
        by_id = {
            entry[1]["annotation"]["document_id"]: entry for entry in _record_entries(run_path)
        }
        decisions_by_id: dict[str, list[dict[str, Any]]] = {}
        for decision in run_payload.get("decisions", []):
            decisions_by_id.setdefault(decision["document_id"], []).append(decision)
        if set(decisions_by_id) - set(by_id):
            raise ValueError(f"Unknown targeted documents in {run_path.name}.")
        for document_id, decisions in decisions_by_id.items():
            _, record = by_id[document_id]
            revised = record["annotation"]
            for decision in decisions:
                revised = apply_replacement(revised, decision)
            row_index = int(record["row_index"])
            jd_input, failure = preprocess_row(
                rows[row_index - 1], row_index, document_id=document_id
            )
            if failure is not None or jd_input is None:
                raise ValueError(f"Cannot reconstruct targeted row {row_index}.")
            annotation = align_all_evidence(
                JDExtractionResult.model_validate(revised), jd_input["source_blocks"]
            )
            validate_semantic_constraints(annotation)

    receipts = []
    for run_payload in payload["runs"]:
        run_path = Path(output_dir) / "runs" / run_payload["run_id"]
        manifest, _, rows = _run_context(run_path, data_root)
        entries = _record_entries(run_path)
        by_id = {entry[1]["annotation"]["document_id"]: entry for entry in entries}
        decisions_by_id: dict[str, list[dict[str, Any]]] = {}
        for decision in run_payload.get("decisions", []):
            decisions_by_id.setdefault(decision["document_id"], []).append(decision)
        if set(decisions_by_id) - set(by_id):
            raise ValueError(f"Unknown targeted documents in {run_path.name}.")

        for document_id, decisions in decisions_by_id.items():
            record_path, record = by_id[document_id]
            revised = record["annotation"]
            for decision in decisions:
                revised = apply_replacement(revised, decision)
            row_index = int(record["row_index"])
            jd_input, failure = preprocess_row(
                rows[row_index - 1], row_index, document_id=document_id
            )
            if failure is not None or jd_input is None:
                raise ValueError(f"Cannot reconstruct targeted row {row_index}.")
            annotation = JDExtractionResult.model_validate(revised)
            annotation = align_all_evidence(annotation, jd_input["source_blocks"])
            validate_semantic_constraints(annotation)
            normalized = normalize_extraction(
                annotation,
                load_normalization_map(normalization_path),
                jd_input["jd_text"],
            )
            record["annotation"] = annotation.model_dump(exclude_none=True)
            record["review_flags"] = [
                *validate_business_rules(annotation),
                *validate_normalized_rules(normalized),
            ]
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

        summary = _rebuild_aggregates(run_path, rows, normalization_path)
        applied_at = utc_now_iso()
        ledger_path = run_path / "targeted_repairs.json"
        ledger = {"version": "1.0", "run_id": run_path.name, "applications": []}
        if ledger_path.exists():
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["applications"].append(
            {
                "applied_at": applied_at,
                "source_path": str(source_path.resolve()),
                "source_path": str(source_path.resolve()),
                "decisions": run_payload.get("decisions", []),
            }
        )
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["targeted_repair_count"] = sum(
            len(item.get("decisions", [])) for item in ledger["applications"]
        )
        manifest["targeted_repair_ledger"] = str(ledger_path)
        manifest["targeted_repair_applied_at"] = applied_at
        (run_path / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        receipts.append({"run_id": run_path.name, "decisions": len(run_payload["decisions"]), **summary})
    return receipts


def reextract_selected_documents(
    selection_path: str | Path,
    output_dir: str | Path,
    data_root: str | Path,
    normalization_path: str,
    model: str,
    semantic_retry_attempts: int = 2,
) -> list[dict[str, Any]]:
    selection = json.loads(Path(selection_path).read_text(encoding="utf-8"))
    if selection.get("version") != "1.0" or not isinstance(selection.get("runs"), list):
        raise ValueError("Targeted extraction selection must use version 1.0 and contain runs.")
    receipts = []
    for run_payload in selection["runs"]:
        run_path = Path(output_dir) / "runs" / run_payload["run_id"]
        manifest, _, rows = _run_context(run_path, data_root)
        entries = _record_entries(run_path)
        by_id = {entry[1]["annotation"]["document_id"]: entry for entry in entries}
        document_ids = run_payload.get("document_ids", [])
        if len(document_ids) != len(set(document_ids)) or set(document_ids) - set(by_id):
            raise ValueError(f"Invalid targeted document selection in {run_path.name}.")
        source_platform = run_payload.get("source_platform")
        if not isinstance(source_platform, str) or not source_platform.strip():
            raise ValueError(f"run {run_path.name} must declare source_platform")
        manifest_platform = manifest.get("source_platform")
        if manifest_platform is not None and manifest_platform != source_platform:
            raise ValueError(
                f"run {run_path.name} source_platform conflicts with manifest: "
                f"{source_platform!r} != {manifest_platform!r}"
            )
        pipeline = JDExtractionPipeline(
            model=model,
            normalization_path=normalization_path,
            max_workers=1,
            semantic_retry_attempts=semantic_retry_attempts,
            source_platform=source_platform,
        )
        audit_records = []
        for document_id in document_ids:
            record_path, record = by_id[document_id]
            row_index = int(record["row_index"])
            jd_input, failure = preprocess_row(
                rows[row_index - 1], row_index, document_id=document_id
            )
            if failure is not None or jd_input is None:
                raise ValueError(f"Cannot reconstruct targeted row {row_index}.")
            outcome = pipeline._extract_validated(jd_input, build_user_prompt(jd_input))
            if outcome.error is not None or outcome.annotation is None:
                raise outcome.error or RuntimeError("Targeted extraction returned no annotation.")
            annotation = deduplicate_extraction(outcome.annotation)
            validate_semantic_constraints(annotation)
            validate_explicit_section_completeness(annotation, jd_input["source_blocks"])
            validate_skill_item_type_contract(annotation, pipeline.norm_map)
            normalized = normalize_extraction(annotation, pipeline.norm_map, jd_input["jd_text"])
            record["annotation"] = annotation.model_dump(exclude_none=True)
            record["review_flags"] = [
                *validate_business_rules(annotation),
                *validate_normalized_rules(normalized),
            ]
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            audit_records.append(
                {
                    "document_id": document_id,
                    "row_index": row_index,
                    "api_attempts": outcome.api_attempts,
                    "extraction_attempts": redact_extraction_attempts(outcome.extraction_attempts),
                    "validation_history": outcome.validation_history,
                    "deterministic_corrections": outcome.deterministic_corrections,
                }
            )

        summary = _rebuild_aggregates(run_path, rows, normalization_path)
        applied_at = utc_now_iso()
        audit_path = run_path / "targeted_extractions.json"
        audit_path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "run_id": run_path.name,
                    "applied_at": applied_at,
                    "model": model,
                    "records": audit_records,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        manifest["targeted_extraction_count"] = len(document_ids)
        manifest["targeted_extraction_audit"] = str(audit_path)
        manifest["targeted_extraction_applied_at"] = applied_at
        (run_path / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        receipts.append({"run_id": run_path.name, "extracted": len(document_ids), **summary})
    return receipts


def sanitize_targeted_extraction_audits(
    selection_path: str | Path,
    output_dir: str | Path,
) -> list[dict[str, Any]]:
    selection = json.loads(Path(selection_path).read_text(encoding="utf-8"))
    if selection.get("version") != "1.0" or not isinstance(selection.get("runs"), list):
        raise ValueError("Targeted extraction selection must use version 1.0 and contain runs.")
    receipts = []
    for run_payload in selection["runs"]:
        audit_path = Path(output_dir) / "runs" / run_payload["run_id"] / "targeted_extractions.json"
        payload = json.loads(audit_path.read_text(encoding="utf-8"))
        records = payload.get("records")
        if payload.get("version") != "1.0" or not isinstance(records, list):
            raise ValueError(f"Invalid targeted extraction audit: {audit_path}")
        for record in records:
            attempts = record.get("extraction_attempts")
            if not isinstance(attempts, list):
                raise ValueError(f"Invalid extraction attempts in audit: {audit_path}")
            record["extraction_attempts"] = redact_extraction_attempts(attempts)
        audit_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        receipts.append({"run_id": run_payload["run_id"], "records": len(records)})
    return receipts
