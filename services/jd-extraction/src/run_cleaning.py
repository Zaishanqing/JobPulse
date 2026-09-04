"""Deterministic cleaning of existing Extraction run artifacts.

The cleaned run keeps the original raw text, adds the canonical cleaned text,
remaps Evidence to the cleaned text, and removes platform watermark artifacts
from semantic values. The output layout mirrors a run package so the main
system full-import flow can consume it directly.
"""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .load_excel import load_excel_rows
from .preprocess import extract_raw_text
from .text_cleaning import clean_jd_text


CLEANING_VERSION = "run-cleaning.v2"
RAW_TEXT_ALIASES = ("原始文本", "jd_text", "JD原文")
_SKIP_SEMANTIC_KEYS = frozenset(
    {
        "evidence",
        "requirement_id",
        "fact_id",
        "source_id",
        "document_id",
        "occurrence_index",
        "raw_quote",
        "raw_start",
        "raw_end",
        "raw_text",
        "cleaned_text",
        "cleaning_status",
        "cleaning_version",
        "cleaning_errors",
        "id",
        "skill_id",
    }
)


def _non_overlapping_occurrences(text: str, quote: str) -> list[int]:
    starts: list[int] = []
    offset = 0
    while True:
        start = text.find(quote, offset)
        if start < 0:
            return starts
        starts.append(start)
        offset = start + len(quote)


def _remap_evidence(cleaned_text: str, evidence: dict[str, Any]) -> str | None:
    raw_start = evidence.get("start")
    raw_end = evidence.get("end")
    raw_quote = str(evidence.get("quote") or "")
    if not raw_quote:
        return "empty_quote"
    cleaned_quote = clean_jd_text(raw_quote)
    if not cleaned_quote:
        return "empty_cleaned_quote"
    if (
        isinstance(raw_start, int)
        and isinstance(raw_end, int)
        and 0 <= raw_start <= raw_end <= len(cleaned_text)
        and cleaned_text[raw_start:raw_end] == cleaned_quote
    ):
        new_start = raw_start
        new_end = raw_end
    else:
        starts = _non_overlapping_occurrences(cleaned_text, cleaned_quote)
        occurrence_index = evidence.get("occurrence_index")
        if isinstance(occurrence_index, int) and occurrence_index < len(starts):
            new_start = starts[occurrence_index]
        elif len(starts) == 1:
            new_start = starts[0]
        else:
            return "cleaned_quote_ambiguous"
        new_end = new_start + len(cleaned_quote)
    occurrence_starts = _non_overlapping_occurrences(cleaned_text, cleaned_quote)
    if new_start not in occurrence_starts:
        return "cleaned_quote_not_found"
    evidence["start"] = new_start
    evidence["end"] = new_end
    evidence["quote"] = cleaned_quote
    evidence["alignment"] = "exact"
    evidence["occurrence_index"] = occurrence_starts.index(new_start)
    return None


def _walk_evidence(payload: Any, cleaned_text: str, failures: list[str]) -> None:
    if isinstance(payload, dict):
        evidence = payload.get("evidence")
        if (
            isinstance(evidence, dict)
            and {"quote", "start", "end"}.issubset(evidence)
        ):
            evidence.setdefault("raw_quote", evidence["quote"])
            evidence.setdefault("raw_start", evidence["start"])
            evidence.setdefault("raw_end", evidence["end"])
            error = _remap_evidence(cleaned_text, evidence)
            if error is not None:
                failures.append(error)
        for value in payload.values():
            _walk_evidence(value, cleaned_text, failures)
    elif isinstance(payload, list):
        for value in payload:
            _walk_evidence(value, cleaned_text, failures)


def _clean_semantic_values(payload: Any) -> bool:
    changed = False
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in _SKIP_SEMANTIC_KEYS:
                continue
            if isinstance(value, str):
                cleaned = clean_jd_text(value)
                if cleaned != value:
                    payload[key] = cleaned
                    changed = True
            elif _clean_semantic_values(value):
                changed = True
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            if isinstance(value, str):
                cleaned = clean_jd_text(value)
                if cleaned != value:
                    payload[index] = cleaned
                    changed = True
            elif _clean_semantic_values(value):
                changed = True
    return changed


def parse_source_blocks(prompt: str) -> list[dict[str, str]] | None:
    marker = "source_blocks: "
    index = prompt.find(marker)
    if index < 0:
        return None
    try:
        blocks, _ = json.JSONDecoder().raw_decode(prompt[index + len(marker) :])
    except json.JSONDecodeError:
        return None
    if not isinstance(blocks, list):
        return None
    result: list[dict[str, str]] = []
    for block in blocks:
        if not isinstance(block, dict) or not block.get("source_id"):
            return None
        result.append(
            {"source_id": str(block["source_id"]), "text": str(block.get("text") or "")}
        )
    return result


def _raw_text_from_audit(audit: dict[str, Any] | None) -> str | None:
    if audit is None:
        return None
    source_row = audit.get("source_row")
    if isinstance(source_row, dict):
        for alias in RAW_TEXT_ALIASES:
            value = source_row.get(alias)
            if isinstance(value, str) and value.strip():
                return value
    prompt = audit.get("user_prompt")
    if isinstance(prompt, str):
        blocks = parse_source_blocks(prompt)
        if blocks:
            return "\n".join(block["text"] for block in blocks)
    return None


def _load_row_raw_text(run_dir: Path, manifest: dict[str, Any]) -> dict[int, str]:
    input_path = manifest.get("input_path")
    if not isinstance(input_path, str) or not input_path.strip():
        return {}
    candidate = Path(input_path)
    if not candidate.is_absolute():
        for base in (run_dir.parents[2], run_dir):
            candidate = base / input_path
            if candidate.is_file():
                break
    if not candidate.is_file():
        return {}
    rows = load_excel_rows(str(candidate))
    return {
        index: (extract_raw_text(row) or "")
        for index, row in enumerate(rows, start=1)
    }


def _success_row_indices(run_dir: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    records_dir = run_dir / "records" / "success"
    if not records_dir.is_dir():
        return result
    for path in records_dir.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        document_id = str(record.get("jd_id") or "")
        row_index = record.get("row_index")
        if document_id and isinstance(row_index, int):
            result[document_id] = row_index
    return result


def clean_annotation_record(
    record: dict[str, Any],
    raw_text: str,
) -> dict[str, Any]:
    cleaned_text = clean_jd_text(raw_text)
    candidate = deepcopy(record)
    failures: list[str] = []
    _walk_evidence(candidate, cleaned_text, failures)
    if failures:
        raise ValueError(
            f"evidence remap failed for {record.get('document_id')}: {failures}"
        )
    semantic_changed = _clean_semantic_values(candidate)
    candidate["raw_text"] = raw_text
    candidate["cleaned_text"] = cleaned_text
    candidate["cleaning_status"] = "ok"
    candidate["cleaning_version"] = CLEANING_VERSION
    if semantic_changed:
        candidate["cleaning_semantic_changed"] = True
    return candidate


def clean_normalized_record(record: dict[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(record)
    semantic_changed = _clean_semantic_values(candidate)
    candidate["cleaning_status"] = "ok"
    candidate["cleaning_version"] = CLEANING_VERSION
    if semantic_changed:
        candidate["cleaning_semantic_changed"] = True
    return candidate


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")


def clean_run(run_dir: Path, output_dir: Path, *, force: bool = False) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        if not force:
            raise FileExistsError(f"cleaned run already exists: {output_dir}")
        shutil.rmtree(output_dir)

    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing manifest.json: {run_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    annotations_path = run_dir / "final" / "annotations_nested.json"
    normalized_path = run_dir / "final" / "normalized_annotations.json"
    if not annotations_path.is_file() or not normalized_path.is_file():
        raise FileNotFoundError(f"missing final artifacts: {run_dir}")
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    if not isinstance(annotations, list) or not isinstance(normalized, list):
        raise ValueError(f"final artifacts must be JSON lists: {run_dir}")

    audits: dict[str, dict[str, Any]] = {}
    audit_dir = run_dir / "audit"
    if audit_dir.is_dir():
        for path in sorted(audit_dir.glob("*.json")):
            audit = json.loads(path.read_text(encoding="utf-8"))
            document_id = str(audit.get("jd_id") or "")
            if document_id:
                audits[document_id] = audit

    cleaned_annotations: list[dict[str, Any]] = []
    row_raw_text: dict[int, str] | None = None
    success_row_indices: dict[str, int] | None = None
    for record in annotations:
        document_id = str(record.get("document_id") or "")
        raw_text = _raw_text_from_audit(audits.get(document_id))
        if not raw_text:
            if success_row_indices is None:
                success_row_indices = _success_row_indices(run_dir)
            if row_raw_text is None:
                row_raw_text = _load_row_raw_text(run_dir, manifest)
            row_index = success_row_indices.get(document_id)
            if row_index is not None:
                raw_text = row_raw_text.get(row_index)
        if not raw_text:
            raise ValueError(
                f"raw text unavailable for {document_id}; cannot clean run"
            )
        cleaned_annotations.append(clean_annotation_record(record, raw_text))

    cleaned_normalized = [
        clean_normalized_record(row) for row in normalized
    ]

    (output_dir / "final").mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "final" / "annotations_nested.json", cleaned_annotations)
    _write_jsonl(output_dir / "final" / "annotations.jsonl", cleaned_annotations)
    _write_json(output_dir / "final" / "normalized_annotations.json", cleaned_normalized)
    _write_jsonl(output_dir / "final" / "normalized_annotations.jsonl", cleaned_normalized)
    for name in ("failed_cases.jsonl", "review_flags.jsonl", "illegal_enum_cases.jsonl"):
        source = run_dir / "final" / name
        if source.is_file():
            shutil.copy2(source, output_dir / "final" / name)
    if audit_dir.is_dir():
        shutil.copytree(audit_dir, output_dir / "audit", dirs_exist_ok=True)

    summary = {
        "record_count": len(cleaned_annotations),
        "cleaned_count": len(cleaned_annotations),
    }
    manifest["cleaned_at"] = datetime.now(timezone.utc).isoformat()
    manifest["cleaning_version"] = CLEANING_VERSION
    manifest["cleaning_summary"] = {
        key: summary[key] for key in ("record_count", "cleaned_count")
    }
    _write_json(output_dir / "manifest.json", manifest)
    _write_json(output_dir / "cleaning_report.json", summary)
    return summary


def clean_runs(
    run_dirs: list[Path],
    output_root: Path,
    *,
    force: bool,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for run_dir in run_dirs:
        if not run_dir.is_dir():
            raise FileNotFoundError(f"run directory does not exist: {run_dir}")
        run_name = run_dir.name
        summary = clean_run(
            run_dir,
            output_root / run_name,
            force=force,
        )
        summary["run_id"] = run_name
        summary["output_dir"] = str(output_root / run_name)
        summaries.append(summary)
    return summaries
