from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_filename(value: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("_", value).strip(" .")
    if not cleaned:
        raise ValueError("Audit filename cannot be empty.")
    return cleaned


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, BaseException):
        return {
            "exception_type": type(value).__name__,
            "message": str(value),
        }
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class RunAudit:
    def __init__(
        self,
        output_dir: Path,
        run_id: str,
        sample_rate: float = 0.1,
    ):
        if sample_rate < 0 or sample_rate > 1:
            raise ValueError("sample_rate must be between 0 and 1.")
        self.output_dir = output_dir
        self.run_id = run_id
        self.sample_rate = sample_rate
        self.run_dir = output_dir / "runs" / run_id
        if self.run_dir.exists():
            runs_root = (output_dir / "runs").resolve()
            resolved_run_dir = self.run_dir.resolve()
            if not resolved_run_dir.is_relative_to(runs_root) or resolved_run_dir == runs_root:
                raise ValueError(f"Refusing to reuse unsafe run directory: {resolved_run_dir}")
        self.audit_dir = self.run_dir / "audit"
        self.final_dir = self.run_dir / "final"
        self.records_dir = self.run_dir / "records"
        self.success_records_dir = self.records_dir / "success"
        self.failed_records_dir = self.records_dir / "failed"
        self.resume_jd_ids = self._load_resume_jd_ids()
        self.logs_path = self.run_dir / "logs.jsonl"
        self.manifest_path = self.run_dir / "manifest.json"
        for directory in (
            self.audit_dir,
            self.final_dir,
            self.success_records_dir,
            self.failed_records_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def _load_resume_jd_ids(self) -> set[str]:
        annotation_ids = self._jsonl_ids("annotations.jsonl")
        normalized_ids = self._jsonl_ids("normalized_annotations.jsonl")
        return annotation_ids & normalized_ids

    def _jsonl_ids(self, filename: str) -> set[str]:
        path = self.final_dir / filename
        if not path.is_file():
            return set()
        ids: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            document_id = record.get("document_id")
            if isinstance(document_id, str) and document_id:
                ids.add(document_id)
        return ids

    def load_jsonl(self, filename: str) -> list[dict[str, Any]]:
        path = self.final_dir / filename
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def load_resume_review_flags(self) -> list[dict[str, Any]]:
        review_flags: list[dict[str, Any]] = []
        if not self.success_records_dir.is_dir():
            return review_flags
        for path in self.success_records_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("jd_id") not in self.resume_jd_ids:
                continue
            flags = payload.get("review_flags")
            if isinstance(flags, list):
                review_flags.extend(
                    flag for flag in flags if isinstance(flag, dict)
                )
        return review_flags

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        payload = {
            "run_id": self.run_id,
            **manifest,
        }
        with self.manifest_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=_json_default)

    def log_event(self, event_type: str, **payload: Any) -> None:
        record = {
            "timestamp": utc_now_iso(),
            "run_id": self.run_id,
            "event_type": event_type,
            **payload,
        }
        with self.logs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")

    def should_audit_jd(self, row_index: int, has_review_flags: bool, failed: bool) -> bool:
        if failed or has_review_flags:
            return True
        if self.sample_rate == 0:
            return False
        interval = max(1, round(1 / self.sample_rate))
        return row_index % interval == 0

    def write_jd_audit(self, jd_id: str, row_index: int, record: dict[str, Any]) -> Path:
        filename = f"{row_index:06d}_{_safe_filename(jd_id)}.json"
        output_path = self.audit_dir / filename
        payload = {
            "run_id": self.run_id,
            "jd_id": jd_id,
            "row_index": row_index,
            **record,
        }
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=_json_default)
        return output_path

    def write_success_record(
        self,
        jd_id: str,
        row_index: int,
        annotation: Any,
        review_flags: list[dict[str, Any]],
        normalized: Any,
    ) -> Path:
        filename = f"{row_index:06d}_{_safe_filename(jd_id)}.json"
        output_path = self.success_records_dir / filename
        payload = {
            "run_id": self.run_id,
            "jd_id": jd_id,
            "row_index": row_index,
            "status": "success",
            "annotation": annotation,
            "normalized": normalized,
            "review_flags": review_flags,
        }
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=_json_default)

        (self.failed_records_dir / filename).unlink(missing_ok=True)
        self.append_jsonl("annotations.jsonl", annotation)
        self.append_jsonl("normalized_annotations.jsonl", normalized)
        for review_flag in review_flags:
            self.append_jsonl("review_flags.jsonl", review_flag)
        return output_path

    def write_failed_record(self, jd_id: str, row_index: int, failed_case: dict[str, Any]) -> Path:
        filename = f"{row_index:06d}_{_safe_filename(jd_id)}.json"
        output_path = self.failed_records_dir / filename
        payload = {
            "run_id": self.run_id,
            "jd_id": jd_id,
            "row_index": row_index,
            "status": "failed",
            "failed_case": failed_case,
        }
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=_json_default)
        (self.success_records_dir / filename).unlink(missing_ok=True)
        self.append_jsonl("failed_cases.jsonl", failed_case)
        return output_path

    def write_illegal_enum_cases(self, records: list[dict[str, Any]]) -> None:
        for record in records:
            self.append_jsonl("illegal_enum_cases.jsonl", record)

    def append_jsonl(self, filename: str, record: Any) -> Path:
        output_path = self.final_dir / filename
        with output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")
        return output_path
