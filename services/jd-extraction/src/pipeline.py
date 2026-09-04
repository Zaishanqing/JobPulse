from __future__ import annotations

import json
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from tqdm import tqdm

from .audit import RunAudit, build_run_id, utc_now_iso
from .document_identity import build_offline_document_id
from .deduplicator import deduplicate_extraction
from .deterministic_fields import canonicalize_authoritative_fields, populate_deterministic_fields
from .deepseek_client import DeepSeekClient, DeepSeekResult
from .exceptions import SchemaValidationError, SemanticValidationError, SourceBindingError
from .exporter import (
    export_failed_cases,
    export_illegal_enum_cases,
    export_jsonl,
    export_nested_json,
    export_review_flags,
    export_xlsx,
)
from .load_excel import load_excel_rows
from .models import JDExtractionResult, JDNormalizedResult
from .normalizer import load_normalization_map, normalize_extraction
from .preprocess import extract_raw_text, preprocess_row
from .local_repair import apply_local_repair, plan_local_repair
from .prompt_builder import (
    build_local_repair_prompt,
    build_system_prompt,
    build_user_prompt,
    build_validation_retry_prompt,
)
from .provenance import align_all_evidence, canonicalize_evidence_quotes
from .requirement_graph import build_requirement_graph
from .report_generator import REPORT_GENERATOR_VERSION, generate_run_report
from .skill_taxonomy import (
    build_classification_records,
    iter_jd_skill_occurrences,
    load_skill_taxonomy_snapshot,
    validate_snapshot_against_normalization_map,
    write_unified_normalized_artifacts,
)
from .validator import (
    BUSINESS_VALIDATOR_VERSION,
    collect_illegal_enum_cases,
    reject_retryable_review_flags,
    validate_business_rules,
    validate_explicit_section_completeness,
    validate_normalized_rules,
    validate_schema,
    validate_semantic_constraints,
    validate_skill_item_type_contract,
)


@dataclass
class _ExtractionOutcome:
    stage: str = "api_call"
    annotation: JDExtractionResult | None = None
    review_flags: list[dict[str, Any]] = field(default_factory=list)
    raw_response: str | None = None
    extracted: dict[str, Any] | None = None
    canonicalized: dict[str, Any] | None = None
    extraction_attempts: list[dict[str, Any]] = field(default_factory=list)
    validation_history: list[dict[str, Any]] = field(default_factory=list)
    retry_mode: str = "initial"
    repair_response: str | None = None
    merged_repair_payload: dict[str, Any] | None = None
    api_attempts: list[dict[str, Any]] = field(default_factory=list)
    retry_events: list[dict[str, Any]] = field(default_factory=list)
    validation_retry_count: int = 0
    local_repair_count: int = 0
    full_reextract_count: int = 0
    local_repair_protocol_rejected_count: int = 0
    deterministic_corrections: list[dict[str, Any]] = field(default_factory=list)
    error: BaseException | None = None


DEFAULT_SKILL_TAXONOMY_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "skill_taxonomy_snapshot.json"
)
class JDExtractionPipeline:
    def __init__(
        self,
        model: str,
        normalization_path: str,
        continue_on_error: bool = False,
        run_id: str | None = None,
        audit_sample_rate: float = 0.1,
        max_workers: int = 10,
        semantic_retry_attempts: int = 2,
        row_indices: set[int] | None = None,
        source_platform: str | None = None,
        skill_taxonomy_path: str | Path = DEFAULT_SKILL_TAXONOMY_PATH,
    ):
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1.")
        if semantic_retry_attempts < 0 or semantic_retry_attempts > 2:
            raise ValueError("semantic_retry_attempts must be between 0 and 2.")
        self.model = model
        self.normalization_path = normalization_path
        self.continue_on_error = continue_on_error
        self.run_id = run_id
        self.audit_sample_rate = audit_sample_rate
        self.max_workers = max_workers
        self.semantic_retry_attempts = semantic_retry_attempts
        self.row_indices = row_indices
        self.source_platform = source_platform
        self.skill_taxonomy_path = Path(skill_taxonomy_path)
        self.system_prompt = build_system_prompt()
        self.client = DeepSeekClient(model=model)
        self.norm_map = load_normalization_map(normalization_path)
        self.skill_taxonomy = load_skill_taxonomy_snapshot(
            self.skill_taxonomy_path
        )
        validate_snapshot_against_normalization_map(
            self.skill_taxonomy, self.norm_map
        )

    def _extract_validated(self, jd_input: dict[str, Any], user_prompt: str) -> _ExtractionOutcome:
        outcome = _ExtractionOutcome()
        result: DeepSeekResult | None = None

        def request(prompt: str, mode: str, attempt: int) -> DeepSeekResult:
            started = perf_counter()
            try:
                return self.client.extract(self.system_prompt, prompt)
            finally:
                outcome.api_attempts.append(
                    {
                        "attempt": attempt,
                        "mode": mode,
                        "elapsed_ms": round((perf_counter() - started) * 1000, 3),
                    }
                )

        try:
            result = request(user_prompt, "initial", 1)
            recoverable_errors = (SchemaValidationError, SourceBindingError, SemanticValidationError)
            attempt_number = 1
            while True:
                outcome.raw_response = result.raw_response
                try:
                    outcome.stage = "postprocess"
                    outcome.extracted = populate_deterministic_fields(
                        deepcopy(result.data), jd_input["jd_id"]
                    )
                    evidence_payload, evidence_corrections = canonicalize_evidence_quotes(
                        result.data,
                        jd_input["source_blocks"],
                    )
                    canonical_payload, authoritative_corrections = canonicalize_authoritative_fields(
                        evidence_payload,
                        self.norm_map,
                        jd_input["source_blocks"],
                    )
                    outcome.canonicalized = populate_deterministic_fields(
                        canonical_payload, jd_input["jd_id"]
                    )
                    outcome.deterministic_corrections = [
                        *evidence_corrections,
                        *authoritative_corrections,
                    ]
                    outcome.stage = "schema_validation"
                    annotation = validate_schema(outcome.canonicalized)
                    outcome.stage = "provenance"
                    annotation = align_all_evidence(annotation, jd_input["source_blocks"])
                    outcome.stage = "semantic_validation"
                    validate_semantic_constraints(annotation)
                    validate_explicit_section_completeness(
                        annotation, jd_input["source_blocks"]
                    )
                    validate_skill_item_type_contract(annotation, self.norm_map)
                    outcome.stage = "business_validation"
                    outcome.review_flags = validate_business_rules(annotation)
                    reject_retryable_review_flags(outcome.review_flags, jd_input["source_blocks"])
                    outcome.extraction_attempts.append(
                        {
                            "attempt": attempt_number,
                            "mode": outcome.retry_mode,
                            "status": "passed",
                            "raw_response": outcome.raw_response,
                        }
                    )
                    outcome.annotation = annotation
                    return outcome
                except recoverable_errors as validation_error:
                    error_details = (
                        validation_error.errors
                        if isinstance(validation_error, SchemaValidationError)
                        else validation_error.violations
                        if isinstance(validation_error, SemanticValidationError)
                        else validation_error.details
                    )
                    outcome.extraction_attempts.append(
                        {
                            "attempt": attempt_number,
                            "mode": outcome.retry_mode,
                            "status": "rejected",
                            "stage": outcome.stage,
                            "error_type": validation_error.__class__.__name__,
                            "error_details": error_details,
                            "raw_response": outcome.raw_response,
                        }
                    )
                    outcome.validation_history.append(
                        {
                            "attempt": attempt_number,
                            "error_type": validation_error.__class__.__name__,
                            "error_details": error_details,
                        }
                    )
                    if outcome.validation_retry_count >= self.semantic_retry_attempts:
                        raise
                    outcome.retry_events.append(
                        {
                            "event_type": "validation_retry_started",
                            "attempt": attempt_number + 1,
                            "rejected_stage": outcome.stage,
                            "error_type": validation_error.__class__.__name__,
                        }
                    )
                    repair_plan = plan_local_repair(
                        outcome.canonicalized,
                        validation_error.__class__.__name__,
                        error_details,
                        jd_input["source_blocks"],
                    )
                    if repair_plan is not None:
                        repair_targets = [
                            {
                                "collection": target.collection,
                                "index": target.index,
                                "current_value": result.data[target.collection][target.index],
                            }
                            for target in repair_plan.targets
                        ]
                        retry_prompt = build_local_repair_prompt(
                            validation_error.__class__.__name__,
                            error_details,
                            repair_targets,
                            list(repair_plan.source_blocks),
                            list(repair_plan.append_collections),
                        )
                        next_retry_mode = "local_repair"
                    else:
                        retry_prompt = build_validation_retry_prompt(
                            jd_input,
                            validation_error.__class__.__name__,
                            error_details,
                            previous_invalid_output=json.dumps(result.data, ensure_ascii=False),
                            validation_history=outcome.validation_history,
                        )
                        next_retry_mode = "full_reextract"
                    outcome.stage = "validation_retry_api_call"
                    retry_result = request(retry_prompt, next_retry_mode, attempt_number + 1)
                    outcome.validation_retry_count += 1
                    if repair_plan is not None:
                        outcome.local_repair_count += 1
                        outcome.repair_response = retry_result.raw_response
                        try:
                            repaired_payload = apply_local_repair(
                                result.data,
                                retry_result.data,
                                repair_plan,
                            )
                        except ValueError as exc:
                            protocol_error = SchemaValidationError(
                                f"Local repair response rejected: {exc}",
                                errors=[{"type": "local_repair_error", "msg": str(exc)}],
                            )
                            outcome.raw_response = retry_result.raw_response
                            outcome.local_repair_protocol_rejected_count += 1
                            attempt_number += 1
                            protocol_details = protocol_error.errors
                            outcome.extraction_attempts.append(
                                {
                                    "attempt": attempt_number,
                                    "mode": "local_repair",
                                    "status": "rejected",
                                    "stage": "local_repair_protocol",
                                    "error_type": protocol_error.__class__.__name__,
                                    "error_details": protocol_details,
                                    "raw_response": retry_result.raw_response,
                                }
                            )
                            outcome.validation_history.append(
                                {
                                    "attempt": attempt_number,
                                    "error_type": protocol_error.__class__.__name__,
                                    "error_details": protocol_details,
                                }
                            )
                            outcome.retry_events.append(
                                {
                                    "event_type": "local_repair_protocol_rejected",
                                    "attempt": attempt_number,
                                    "error_details": protocol_details,
                                }
                            )
                            if outcome.validation_retry_count >= self.semantic_retry_attempts:
                                raise protocol_error from exc
                            outcome.retry_events.append(
                                {
                                    "event_type": "validation_retry_started",
                                    "attempt": attempt_number + 1,
                                    "rejected_stage": "local_repair_protocol",
                                    "error_type": protocol_error.__class__.__name__,
                                }
                            )
                            retry_prompt = build_validation_retry_prompt(
                                jd_input,
                                protocol_error.__class__.__name__,
                                protocol_details,
                                previous_invalid_output=json.dumps(result.data, ensure_ascii=False),
                                validation_history=outcome.validation_history,
                            )
                            outcome.stage = "validation_retry_api_call"
                            result = request(retry_prompt, "full_reextract", attempt_number + 1)
                            outcome.validation_retry_count += 1
                            outcome.full_reextract_count += 1
                            outcome.retry_mode = "full_reextract"
                            attempt_number += 1
                            continue
                        result = DeepSeekResult(data=repaired_payload, raw_response=retry_result.raw_response)
                        outcome.merged_repair_payload = deepcopy(repaired_payload)
                    else:
                        result = retry_result
                        outcome.full_reextract_count += 1
                    outcome.retry_mode = next_retry_mode
                    attempt_number += 1
        except Exception as exc:
            outcome.raw_response = outcome.raw_response or getattr(exc, "raw_response", None)
            outcome.error = exc
            return outcome

    def run(self, input_xlsx: str, output_dir: str) -> None:
        if not isinstance(self.source_platform, str) or not self.source_platform.strip():
            raise ValueError("source_platform is required for offline extraction runs.")
        rows = load_excel_rows(input_xlsx)
        indexed_rows = list(enumerate(rows, start=1))
        if self.row_indices is not None:
            missing = self.row_indices - {index for index, _ in indexed_rows}
            if missing:
                raise ValueError(f"Requested row indices do not exist: {sorted(missing)}")
            indexed_rows = [(index, row) for index, row in indexed_rows if index in self.row_indices]
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        run_id = self.run_id or build_run_id()
        audit = RunAudit(output_path, run_id, sample_rate=self.audit_sample_rate)
        run_started_at = utc_now_iso()
        manifest: dict[str, Any] = {
            "run_started_at": run_started_at,
            "input_path": str(input_xlsx),
            "output_dir": str(output_path),
            "model": self.model,
            "source_platform": self.source_platform,
            "normalization_path": str(Path(self.normalization_path).resolve()),
            "continue_on_error": self.continue_on_error,
            "audit_sample_rate": self.audit_sample_rate,
            "max_workers": self.max_workers,
            "semantic_retry_attempts": self.semantic_retry_attempts,
            "total_rows": len(indexed_rows),
            "selected_row_indices": [index for index, _ in indexed_rows],
            "extraction_schema_version": "2.0",
            "normalization_taxonomy_version": self.norm_map["version"],
            "skill_taxonomy_snapshot_path": str(self.skill_taxonomy_path.resolve()),
            "business_validator_version": BUSINESS_VALIDATOR_VERSION,
            "report_generator_version": REPORT_GENERATOR_VERSION,
        }
        audit.write_manifest(manifest)
        audit.log_event(
            "run_started",
            input_path=str(input_xlsx),
            output_dir=str(output_path),
            total_rows=len(indexed_rows),
            model=self.model,
            max_workers=self.max_workers,
        )

        annotations: list[JDExtractionResult] = []
        normalized_results: list[JDNormalizedResult] = []
        all_review_flags: list[dict[str, Any]] = []
        failed_cases: list[dict[str, Any]] = []
        illegal_enum_cases: list[dict[str, Any]] = []
        resume_skipped_count = 0
        resume_skipped_row_indices: list[int] = []
        resumed_normalized_payloads: list[dict[str, Any]] = []
        if audit.resume_jd_ids:
            annotations = [
                JDExtractionResult.model_validate(item)
                for item in audit.load_jsonl("annotations.jsonl")
                if item.get("document_id") in audit.resume_jd_ids
            ]
            resumed_normalized_payloads = audit.load_jsonl(
                "normalized_annotations.jsonl"
            )
            resumed_normalized_payloads = [
                item
                for item in resumed_normalized_payloads
                if item.get("document_id") in audit.resume_jd_ids
            ]
            all_review_flags = audit.load_resume_review_flags()
        api_call_count = 0
        validation_retry_count = 0
        local_repair_count = 0
        full_reextract_count = 0
        local_repair_protocol_rejected_count = 0
        local_repair_recovered_count = 0
        validation_retry_recovered_count = 0
        initial_pass_count = 0
        recovered_after_first_retry_count = 0
        recovered_after_second_retry_count = 0
        validation_retry_exhausted_count = 0
        deterministic_correction_count = 0

        executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="jd-api")
        prepared_rows: list[dict[str, Any]] = []
        for row_index, row in indexed_rows:
            raw_text = extract_raw_text(row) or ""
            initial_jd_id = build_offline_document_id(
                self.source_platform,
                input_xlsx,
                row_index,
                raw_text,
            )
            audit.log_event("row_started", row_index=row_index, jd_id=initial_jd_id)
            if initial_jd_id in audit.resume_jd_ids:
                resume_skipped_count += 1
                resume_skipped_row_indices.append(row_index)
                audit.log_event(
                    "row_skipped",
                    row_index=row_index,
                    jd_id=initial_jd_id,
                    reason="already_succeeded",
                )
                prepared_rows.append(
                    {
                        "row_index": row_index,
                        "row": row,
                        "document_id": initial_jd_id,
                        "skipped": True,
                    }
                )
                continue
            prepared: dict[str, Any] = {
                "row_index": row_index,
                "row": row,
                "document_id": initial_jd_id,
                "started_at": perf_counter(),
                "jd_input": None,
                "failed_case": None,
                "user_prompt": None,
                "future": None,
                "preprocess_error": None,
            }
            try:
                jd_input, failed_case = preprocess_row(
                    row,
                    row_index,
                    document_id=initial_jd_id,
                )
                prepared["jd_input"] = jd_input
                prepared["failed_case"] = failed_case
                if jd_input is not None:
                    user_prompt = build_user_prompt(jd_input)
                    prepared["user_prompt"] = user_prompt
                    prepared["future"] = executor.submit(
                        self._extract_validated,
                        jd_input,
                        user_prompt,
                    )
            except Exception as exc:
                prepared["preprocess_error"] = exc
            prepared_rows.append(prepared)

        for prepared in tqdm(prepared_rows, desc="Processing JDs"):
            if prepared.get("skipped"):
                continue
            row_index = prepared["row_index"]
            row = prepared["row"]
            row_started = prepared["started_at"]
            stage = "preprocess"
            jd_id = prepared["document_id"]
            jd_input: dict[str, Any] | None = None
            user_prompt: str | None = None
            raw_response: str | None = None
            extracted: dict[str, Any] | None = None
            canonicalized: dict[str, Any] | None = None
            review_flags: list[dict[str, Any]] = []
            final_annotation: JDExtractionResult | None = None
            extraction_attempts: list[dict[str, Any]] = []
            retry_mode = "initial"
            repair_response: str | None = None
            merged_repair_payload: dict[str, Any] | None = None
            deterministic_corrections: list[dict[str, Any]] = []
            try:
                if prepared["preprocess_error"] is not None:
                    raise prepared["preprocess_error"]
                jd_input = prepared["jd_input"]
                failed_case = prepared["failed_case"]
                if failed_case is not None:
                    failed_cases.append(failed_case)
                    audit.write_failed_record(failed_case["jd_id"], row_index, failed_case)
                    audit.log_event(
                        "row_failed",
                        row_index=row_index,
                        jd_id=failed_case["jd_id"],
                        stage=stage,
                        error_type=failed_case["error_type"],
                        elapsed_ms=round((perf_counter() - row_started) * 1000, 3),
                    )
                    audit.write_jd_audit(
                        failed_case["jd_id"],
                        row_index,
                        {
                            "audit_reason": "failed",
                            "stage": stage,
                            "source_row": row,
                            "error": failed_case,
                        },
                    )
                    continue

                jd_id = jd_input["jd_id"]
                stage = "build_prompt"
                user_prompt = prepared["user_prompt"]
                stage = "api_call"
                future: Future = prepared["future"]
                outcome: _ExtractionOutcome = future.result()
                stage = outcome.stage
                raw_response = outcome.raw_response
                extracted = outcome.extracted
                canonicalized = outcome.canonicalized
                review_flags = outcome.review_flags
                extraction_attempts = outcome.extraction_attempts
                retry_mode = outcome.retry_mode
                repair_response = outcome.repair_response
                merged_repair_payload = outcome.merged_repair_payload
                deterministic_corrections = outcome.deterministic_corrections
                deterministic_correction_count += len(deterministic_corrections)
                api_call_count += len(outcome.api_attempts)
                validation_retry_count += outcome.validation_retry_count
                local_repair_count += outcome.local_repair_count
                full_reextract_count += outcome.full_reextract_count
                local_repair_protocol_rejected_count += outcome.local_repair_protocol_rejected_count
                for event in outcome.retry_events:
                    event_payload = dict(event)
                    event_type = event_payload.pop("event_type")
                    audit.log_event(event_type, row_index=row_index, jd_id=jd_id, **event_payload)
                for api_attempt in outcome.api_attempts:
                    audit.log_event(
                        "api_attempt_finished",
                        row_index=row_index,
                        jd_id=jd_id,
                        **api_attempt,
                    )
                if outcome.error is not None:
                    raise outcome.error
                annotation = outcome.annotation
                if annotation is None:
                    raise RuntimeError("Validated extraction completed without an annotation.")
                audit.log_event(
                    "api_call_finished",
                    row_index=row_index,
                    jd_id=jd_id,
                    attempt_count=len(extraction_attempts),
                    elapsed_ms=round((perf_counter() - row_started) * 1000, 3),
                )

                stage = "deduplication"
                annotation = deduplicate_extraction(annotation)
                annotation.requirement_graph = build_requirement_graph(
                    annotation, jd_input["source_blocks"]
                )
                stage = "normalization"
                normalized = normalize_extraction(annotation, self.norm_map, jd_input["jd_text"])
                review_flags.extend(validate_normalized_rules(normalized))
                final_annotation = annotation

                annotations.append(annotation)
                normalized_results.append(normalized)
                all_review_flags.extend(review_flags)
                audit.write_success_record(
                    jd_id,
                    row_index,
                    annotation,
                    review_flags,
                    normalized,
                )
                has_review_flags = bool(review_flags)
                has_deterministic_corrections = bool(deterministic_corrections)
                had_validation_retry = len(extraction_attempts) > 1
                if had_validation_retry:
                    validation_retry_recovered_count += 1
                if extraction_attempts[-1]["mode"] == "local_repair":
                    local_repair_recovered_count += 1
                if len(extraction_attempts) == 1:
                    initial_pass_count += 1
                elif len(extraction_attempts) == 2:
                    recovered_after_first_retry_count += 1
                elif len(extraction_attempts) == 3:
                    recovered_after_second_retry_count += 1
                should_audit = has_deterministic_corrections or had_validation_retry or audit.should_audit_jd(
                    row_index, has_review_flags=has_review_flags, failed=False
                )
                audit.log_event(
                    "row_succeeded",
                    row_index=row_index,
                    jd_id=jd_id,
                    review_flag_count=len(review_flags),
                    deterministic_correction_count=len(deterministic_corrections),
                    validation_retry_count=max(0, len(extraction_attempts) - 1),
                    audited=should_audit,
                    elapsed_ms=round((perf_counter() - row_started) * 1000, 3),
                )
                if should_audit:
                    audit.write_jd_audit(
                        jd_id,
                        row_index,
                        {
                            "audit_reason": (
                                "validation_retry"
                                if had_validation_retry
                                else "deterministic_correction"
                                if has_deterministic_corrections
                                else "review_flag"
                                if has_review_flags
                                else "sampled_success"
                            ),
                            "stage": "completed",
                            "source_row": row,
                            "user_prompt": user_prompt,
                            "raw_response": raw_response,
                            "raw_response_kind": retry_mode,
                            "repair_response": repair_response,
                            "merged_repair_payload": merged_repair_payload,
                            "extracted_json": extracted,
                            "canonicalized_json": canonicalized,
                            "schema_status": "passed",
                            "review_flags": review_flags,
                            "final_annotation": final_annotation,
                            "normalized_result": normalized,
                            "extraction_attempts": extraction_attempts,
                            "deterministic_corrections": deterministic_corrections,
                        },
                    )
            except Exception as exc:
                if extraction_attempts and all(
                    attempt.get("status") == "rejected" for attempt in extraction_attempts
                ):
                    validation_retry_exhausted_count += 1
                raw_response = raw_response or getattr(exc, "raw_response", None)
                failed_jd_id = jd_input["jd_id"] if jd_input is not None else jd_id
                failed_case = {
                    "jd_id": failed_jd_id,
                    "row_index": row_index,
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                    "stage": stage,
                }
                row_illegal_enum_cases: list[dict[str, Any]] = []
                if isinstance(exc, SchemaValidationError) and canonicalized is not None:
                    row_illegal_enum_cases = collect_illegal_enum_cases(
                        canonicalized,
                        exc.errors,
                        failed_jd_id,
                        row_index,
                    )
                    illegal_enum_cases.extend(row_illegal_enum_cases)
                    audit.write_illegal_enum_cases(row_illegal_enum_cases)
                    failed_case["illegal_enum_case_count"] = len(row_illegal_enum_cases)
                audit.log_event(
                    "row_failed",
                    row_index=row_index,
                    jd_id=failed_jd_id,
                    stage=stage,
                    error_type=exc.__class__.__name__,
                    elapsed_ms=round((perf_counter() - row_started) * 1000, 3),
                )
                audit.write_jd_audit(
                    failed_jd_id,
                    row_index,
                    {
                        "audit_reason": "failed",
                        "stage": stage,
                        "source_row": row,
                        "user_prompt": user_prompt,
                        "raw_response": raw_response,
                        "raw_response_kind": retry_mode,
                        "repair_response": repair_response,
                        "merged_repair_payload": merged_repair_payload,
                        "extracted_json": extracted,
                        "canonicalized_json": canonicalized,
                        "schema_status": "failed" if stage == "schema_validation" else "not_reached",
                        "review_flags": review_flags,
                        "error": failed_case,
                        "illegal_enum_cases": row_illegal_enum_cases,
                        "extraction_attempts": extraction_attempts,
                        "deterministic_corrections": deterministic_corrections,
                    },
                )
                if not self.continue_on_error:
                    audit.write_failed_record(failed_jd_id, row_index, failed_case)
                    manifest.update(
                        {
                            "run_failed_at": utc_now_iso(),
                            "success_count": len(annotations),
                            "failed_count": len(failed_cases) + 1,
                            "review_flag_count": len(all_review_flags),
                            "api_call_count": api_call_count,
                            "validation_retry_count": validation_retry_count,
                            "local_repair_count": local_repair_count,
                            "full_reextract_count": full_reextract_count,
                            "local_repair_protocol_rejected_count": local_repair_protocol_rejected_count,
                            "local_repair_recovered_count": local_repair_recovered_count,
                            "deterministic_correction_count": deterministic_correction_count,
                            "failed_row_index": row_index,
                            "failed_jd_id": failed_jd_id,
                            "failed_stage": stage,
                            "failed_error_type": exc.__class__.__name__,
                        }
                    )
                    audit.write_manifest(manifest)
                    audit.log_event(
                        "run_aborted",
                        row_index=row_index,
                        jd_id=failed_jd_id,
                        stage=stage,
                        error_type=exc.__class__.__name__,
                    )
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise
                failed_cases.append(failed_case)
                audit.write_failed_record(failed_jd_id, row_index, failed_case)

        executor.shutdown(wait=True)
        final_dir = audit.final_dir
        export_jsonl(annotations, str(final_dir / "annotations.jsonl"))
        export_nested_json(annotations, str(final_dir / "annotations_nested.json"))
        classification_records, classification_summary = build_classification_records(
            iter_jd_skill_occurrences(normalized_results), self.skill_taxonomy
        )
        unified_normalized_results = write_unified_normalized_artifacts(
            normalized_results,
            classification_records,
            self.skill_taxonomy,
            final_dir,
        )
        if audit.resume_jd_ids:
            unified_normalized_results = [
                *resumed_normalized_payloads,
                *unified_normalized_results,
            ]
            (final_dir / "normalized_annotations.jsonl").write_text(
                "".join(
                    json.dumps(item, ensure_ascii=False) + "\n"
                    for item in unified_normalized_results
                ),
                encoding="utf-8",
            )
            (final_dir / "normalized_annotations.json").write_text(
                json.dumps(unified_normalized_results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        export_xlsx(
            annotations,
            unified_normalized_results,
            all_review_flags,
            str(final_dir / "annotations.xlsx"),
        )
        export_failed_cases(failed_cases, str(final_dir / "failed_cases.jsonl"))
        export_review_flags(all_review_flags, str(final_dir / "review_flags.jsonl"))
        export_illegal_enum_cases(illegal_enum_cases, str(final_dir / "illegal_enum_cases.jsonl"))
        manifest.update(
            {
                "run_finished_at": utc_now_iso(),
                "success_count": len(annotations),
                "failed_count": len(failed_cases),
                "review_flag_count": len(all_review_flags),
                "api_call_count": api_call_count,
                "illegal_enum_case_count": len(illegal_enum_cases),
                "validation_retry_count": validation_retry_count,
                "local_repair_count": local_repair_count,
                "full_reextract_count": full_reextract_count,
                "local_repair_protocol_rejected_count": local_repair_protocol_rejected_count,
                "local_repair_recovered_count": local_repair_recovered_count,
                "validation_retry_recovered_count": validation_retry_recovered_count,
                "initial_pass_count": initial_pass_count,
                "recovered_after_first_retry_count": recovered_after_first_retry_count,
                "recovered_after_second_retry_count": recovered_after_second_retry_count,
                "validation_retry_exhausted_count": validation_retry_exhausted_count,
                "deterministic_correction_count": deterministic_correction_count,
                "resume_skipped_count": resume_skipped_count,
                "resume_skipped_row_indices": resume_skipped_row_indices,
                **classification_summary,
            }
        )
        audit.write_manifest(manifest)
        audit.log_event(
            "run_finished",
            success_count=len(annotations),
            failed_count=len(failed_cases),
            review_flag_count=len(all_review_flags),
            api_call_count=api_call_count,
            illegal_enum_case_count=len(illegal_enum_cases),
            validation_retry_count=validation_retry_count,
            local_repair_count=local_repair_count,
            full_reextract_count=full_reextract_count,
            local_repair_protocol_rejected_count=local_repair_protocol_rejected_count,
            local_repair_recovered_count=local_repair_recovered_count,
            validation_retry_recovered_count=validation_retry_recovered_count,
            initial_pass_count=initial_pass_count,
            recovered_after_first_retry_count=recovered_after_first_retry_count,
            recovered_after_second_retry_count=recovered_after_second_retry_count,
            validation_retry_exhausted_count=validation_retry_exhausted_count,
            deterministic_correction_count=deterministic_correction_count,
        )
        report_started = perf_counter()
        report_path = generate_run_report(audit.run_dir)
        report_elapsed_ms = round((perf_counter() - report_started) * 1000, 3)
        manifest["research_report_path"] = str(report_path)
        manifest["research_report_elapsed_ms"] = report_elapsed_ms
        audit.write_manifest(manifest)
        audit.log_event(
            "research_report_generated",
            report_path=str(report_path),
            elapsed_ms=report_elapsed_ms,
        )
