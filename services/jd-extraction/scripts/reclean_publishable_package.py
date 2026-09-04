from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.run_cleaning import (  # noqa: E402
    clean_annotation_record,
    clean_normalized_record,
)


def _read_json(archive: zipfile.ZipFile, name: str) -> Any:
    return json.loads(archive.read(name).decode("utf-8"))


def _read_jsonl(archive: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in archive.read(name).decode("utf-8").split("\n")
        if line.strip()
    ]


def _write_json(archive: zipfile.ZipFile, name: str, value: Any) -> None:
    archive.writestr(name, json.dumps(value, ensure_ascii=False, indent=2))


def _write_jsonl(
    archive: zipfile.ZipFile,
    name: str,
    rows: list[dict[str, Any]],
) -> None:
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    archive.writestr(name, payload)


def _verify_evidence_alignment(
    annotations: list[dict[str, Any]],
) -> tuple[int, int]:
    total = 0
    changed = 0
    for record in annotations:
        cleaned = str(record["cleaned_text"])

        def walk(payload: Any) -> None:
            nonlocal total, changed
            if isinstance(payload, dict):
                evidence = payload.get("evidence")
                if (
                    isinstance(evidence, dict)
                    and {"quote", "start", "end"}.issubset(evidence)
                ):
                    total += 1
                    start = int(evidence["start"])
                    end = int(evidence["end"])
                    quote = str(evidence["quote"])
                    if cleaned[start:end] != quote:
                        raise AssertionError(
                            f"evidence not aligned in {record['document_id']}"
                        )
                    if quote != str(evidence.get("raw_quote", "")):
                        changed += 1
                for value in payload.values():
                    walk(value)
            elif isinstance(payload, list):
                for value in payload:
                    walk(value)

        walk(record)
    return total, changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-clean an existing publishable package with the current "
            "run-cleaning rules and write a new package with remapped Evidence."
        )
    )
    parser.add_argument("--source-package", type=Path, required=True)
    parser.add_argument("--output-zip", type=Path, required=True)
    args = parser.parse_args()

    source_root = args.source_package.stem
    with zipfile.ZipFile(args.source_package) as source:
        annotations = _read_jsonl(
            source, f"{source_root}/final/annotations.jsonl"
        )
        normalized = _read_jsonl(
            source, f"{source_root}/final/normalized_annotations.jsonl"
        )
        current_ids = _read_json(
            source, f"{source_root}/audit/current_document_ids.json"
        )
        position_distribution = _read_json(
            source, f"{source_root}/audit/position_distribution.json"
        )
        old_manifest = _read_json(source, f"{source_root}/manifest.json")

    recleaned_annotations = []
    cleaned_text_changed = 0
    for record in annotations:
        old_cleaned = record.get("cleaned_text")
        record.pop("cleaning_semantic_changed", None)
        cleaned = clean_annotation_record(record, str(record["raw_text"]))
        if old_cleaned != cleaned.get("cleaned_text"):
            cleaned_text_changed += 1
        recleaned_annotations.append(cleaned)

    recleaned_normalized = []
    for record in normalized:
        record.pop("cleaning_semantic_changed", None)
        recleaned_normalized.append(clean_normalized_record(record))

    evidence_count, evidence_changed = _verify_evidence_alignment(
        recleaned_annotations
    )
    if len(recleaned_annotations) != len(annotations):
        raise AssertionError("annotation count changed during re-cleaning")
    if len(recleaned_normalized) != len(normalized):
        raise AssertionError("normalized count changed during re-cleaning")

    generated_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        **old_manifest,
        "created_at": generated_at,
        "source_package": args.source_package.name,
        "cleaning_version": "run-cleaning.v2",
    }
    cleaning_report = {
        "schema": "position-cleaned-reclean-report.v1",
        "generated_at": generated_at,
        "cleaning_version": "run-cleaning.v2",
        "source_package": args.source_package.name,
        "record_count": len(recleaned_annotations),
        "evidence_count": evidence_count,
        "evidence_remap_failures": 0,
        "cleaned_text_changed_count": cleaned_text_changed,
        "evidence_changed_count": evidence_changed,
    }

    output_root = args.output_zip.stem
    args.output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        args.output_zip, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        _write_jsonl(archive, f"{output_root}/final/annotations.jsonl", recleaned_annotations)
        _write_json(archive, f"{output_root}/final/annotations_nested.json", recleaned_annotations)
        _write_jsonl(archive, f"{output_root}/final/normalized_annotations.jsonl", recleaned_normalized)
        _write_json(archive, f"{output_root}/final/normalized_annotations.json", recleaned_normalized)
        for name in (
            "final/review_flags.jsonl",
            "final/failed_cases.jsonl",
            "final/illegal_enum_cases.jsonl",
        ):
            archive.writestr(f"{output_root}/{name}", "")
        _write_json(archive, f"{output_root}/audit/current_document_ids.json", current_ids)
        _write_json(archive, f"{output_root}/audit/position_distribution.json", position_distribution)
        _write_json(archive, f"{output_root}/manifest.json", manifest)
        _write_json(archive, f"{output_root}/cleaning_report.json", cleaning_report)

    print(json.dumps(cleaning_report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
