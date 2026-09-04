from __future__ import annotations

import json
import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from dotenv import load_dotenv
from tqdm import tqdm

from .audit import RunAudit, build_run_id, utc_now_iso
from .capability_verification import (
    CAPABILITY_VERIFICATION_DERIVATION_VERSION,
    build_capability_verification,
)
from .checkpoints import SQLiteExtractionCheckpointStore, build_content_checkpoint_key
from .deduplicator import deduplicate_extraction
from .deepseek_client import (
    DeepSeekClient,
    DeepSeekResult,
    DeepSeekTimeoutError,
    InvalidJSONError,
)
from .deterministic_fields import (
    canonicalize_authoritative_fields,
    populate_deterministic_fields,
)
from .exceptions import (
    CandidateValidationError,
    SchemaValidationError,
    SemanticValidationError,
    SourceBindingError,
    InputFormatError,
)
from .exporter import (
    export_capability_evidence_links_jsonl,
    export_capability_profiles_jsonl,
    export_capability_verification_profiles,
    export_failed_cases,
    export_illegal_enum_cases,
    export_jsonl,
    export_match_feature_profiles,
    export_match_features_jsonl,
    export_nested_json,
    export_review_flags,
    export_xlsx,
)
from .field_contract import FIELD_CONTRACT_VERSION
from .local_repair import apply_local_repair, plan_local_repair
from .load_excel import load_excel_rows
from .match_features import MATCH_FEATURE_DERIVATION_VERSION, build_cv_match_features
from .models import (
    CVCapabilityVerificationResult,
    CVExtractionResult,
    CVMatchFeatureResult,
    CVNormalizedResult,
)
from .normalizer import load_normalization_map, normalize_extraction
from .preprocess import preprocess_row
from .prompt_builder import (
    build_local_repair_prompt,
    build_system_prompt,
    build_user_prompt,
    build_validation_retry_prompt,
)
from .provenance import (
    align_all_evidence,
    canonicalize_evidence_quotes,
    collect_payload_evidence_binding_errors,
)
from .report_generator import REPORT_GENERATOR_VERSION, generate_run_report
from .skill_taxonomy import (
    build_classification_records,
    iter_cv_skill_occurrences,
    load_skill_taxonomy_snapshot,
    validate_snapshot_against_normalization_map,
    write_unified_normalized_artifacts,
)
from .validator import (
    BUSINESS_VALIDATOR_VERSION,
    collect_raw_match_field_evidence_violations,
    collect_raw_semantic_violations,
    collect_source_coverage_requirements,
    collect_source_taxonomy_requirements,
    collect_skill_evidence_support_violations,
    collect_taxonomy_skill_coverage_violations,
    collect_source_section_coverage_violations,
    collect_illegal_enum_cases,
    validate_business_rules,
    validate_normalized_rules,
    validate_schema,
    validate_semantic_constraints,
    validate_skill_item_type_contract,
)


@dataclass
class _ExtractionOutcome:
    stage: str = "api_call"
    annotation: CVExtractionResult | None = None
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


@dataclass(frozen=True)
class CVSingleExtraction:
    extraction: CVExtractionResult
    normalized: CVNormalizedResult
    review_flags: tuple[dict[str, Any], ...]
    api_attempts: tuple[dict[str, Any], ...] = ()
    semantic_repair_count: int = 0


DEFAULT_SKILL_TAXONOMY_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "taxonomy"
    / "2.0"
    / "skill_taxonomy_snapshot.json"
)
SHARED_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"

# Each shard receives the complete OCR text, while its output contract is limited
# to a disjoint group of fields. This keeps cross-section context available and
# shortens the serial generation path before the merged payload is validated.
SECTION_EXTRACTION_SHARDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("identity_history", ("personal_info", "education", "work_experience")),
    ("projects", ("project_experience",)),
    (
        "skills_credentials",
        (
            "skills",
            "languages",
            "certificates",
            "awards",
        ),
    ),
    (
        "research_summary",
        (
            "publications",
            "patents",
            "research_outputs",
            "self_evaluation",
        ),
    ),
)

LOGGER = logging.getLogger(__name__)


class CVExtractionPipeline:
    def __init__(
        self,
        model: str,
        normalization_path: str,
        continue_on_error: bool = False,
        run_id: str | None = None,
        audit_sample_rate: float = 0.1,
        max_workers: int = 20,
        semantic_retry_attempts: int = 2,
        api_timeout_seconds: int = 300,
        task_budget_seconds: int | None = None,
        skill_taxonomy_path: str | Path = DEFAULT_SKILL_TAXONOMY_PATH,
        parallel_section_extraction: bool = False,
        checkpoint_store: SQLiteExtractionCheckpointStore | None = None,
        checkpoint_fingerprint: dict[str, str] | None = None,
    ):
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1.")
        if semantic_retry_attempts < 0 or semantic_retry_attempts > 2:
            raise ValueError("semantic_retry_attempts must be between 0 and 2.")
        if api_timeout_seconds < 1:
            raise ValueError("api_timeout_seconds must be at least 1.")
        if task_budget_seconds is not None and task_budget_seconds < 1:
            raise ValueError("task_budget_seconds must be at least 1.")
        load_dotenv(SHARED_ENV_PATH, override=True)
        self.model = model
        self.normalization_path = normalization_path
        self.continue_on_error = continue_on_error
        self.run_id = run_id
        self.audit_sample_rate = audit_sample_rate
        self.max_workers = max_workers
        self.semantic_retry_attempts = semantic_retry_attempts
        self.api_timeout_seconds = api_timeout_seconds
        self.task_budget_seconds = task_budget_seconds
        self._task_started: float | None = None
        self.checkpoint_store = checkpoint_store
        self.checkpoint_fingerprint = checkpoint_fingerprint or {}
        self.parallel_section_extraction = parallel_section_extraction
        self.skill_taxonomy_path = Path(skill_taxonomy_path)
        self.system_prompt = build_system_prompt()
        # HTTP CV extraction is already retried by its durable task queue. Keep
        # the provider call single-shot so nested transport/JSON retries cannot
        # multiply one task into a many-minute request.
        self.client = DeepSeekClient(
            model=model,
            timeout=api_timeout_seconds,
            transport_attempts=1,
            json_attempts=1,
        )
        self.norm_map = load_normalization_map(normalization_path)
        self.skill_taxonomy = load_skill_taxonomy_snapshot(
            self.skill_taxonomy_path
        )
        validate_snapshot_against_normalization_map(
            self.skill_taxonomy, self.norm_map
        )

    def _extract_validated(
        self,
        cv_input: dict[str, Any],
        user_prompt: str,
        *,
        initial_result: DeepSeekResult | None = None,
        initial_api_attempts: tuple[dict[str, Any], ...] = (),
    ) -> _ExtractionOutcome:
        outcome = _ExtractionOutcome()
        outcome.api_attempts.extend(initial_api_attempts)
        result: DeepSeekResult | None = None
        taxonomy_requirements = collect_source_taxonomy_requirements(
            self.norm_map, cv_input["source_blocks"]
        )
        coverage_requirements = collect_source_coverage_requirements(
            cv_input["source_blocks"]
        )

        def request(prompt: str, mode: str, attempt: int) -> DeepSeekResult:
            # 重试类调用受任务总预算约束：超预算就快速失败，让上层队列重试时
            # 复用已落盘的 section checkpoint，而不是挂到被调用方读超时掐断。
            if (
                self.task_budget_seconds is not None
                and self._task_started is not None
                and outcome.api_attempts
            ):
                budget_elapsed = perf_counter() - self._task_started
                if budget_elapsed > self.task_budget_seconds:
                    raise DeepSeekTimeoutError(
                        "CV extraction task budget exhausted before retry call "
                        f"(elapsed={budget_elapsed:.1f}s, budget={self.task_budget_seconds}s)"
                    )
            started = perf_counter()
            transport_attempt_count = 0
            try:
                response = self.client.extract(self.system_prompt, prompt)
                transport_attempt_count = response.transport_attempt_count
                return response
            finally:
                elapsed_ms = round((perf_counter() - started) * 1000, 3)
                outcome.api_attempts.append(
                    {
                        "attempt": attempt,
                        "mode": mode,
                        "elapsed_ms": elapsed_ms,
                        "transport_attempt_count": transport_attempt_count,
                    }
                )
                LOGGER.info(
                    "cv_extraction_api_attempt mode=%s attempt=%s elapsed_ms=%s transport_attempts=%s",
                    mode,
                    attempt,
                    elapsed_ms,
                    transport_attempt_count,
                )

        try:
            result = initial_result or request(user_prompt, "initial", 1)
            recoverable_errors = (
                CandidateValidationError,
                SchemaValidationError,
                SourceBindingError,
                SemanticValidationError,
            )
            attempt_number = 1
            while True:
                outcome.raw_response = result.raw_response
                try:
                    outcome.stage = "postprocess"
                    outcome.extracted = populate_deterministic_fields(
                        deepcopy(result.data), cv_input["cv_id"]
                    )
                    evidence_payload, evidence_corrections = canonicalize_evidence_quotes(
                        result.data,
                        cv_input["source_blocks"],
                    )
                    canonical_payload, authoritative_corrections = canonicalize_authoritative_fields(
                        evidence_payload,
                        self.norm_map,
                    )
                    outcome.canonicalized = populate_deterministic_fields(
                        canonical_payload, cv_input["cv_id"]
                    )
                    outcome.deterministic_corrections = [
                        *evidence_corrections,
                        *authoritative_corrections,
                    ]
                    outcome.stage = "candidate_validation"
                    validation_issues: list[dict[str, Any]] = []
                    coverage_violations = collect_source_section_coverage_violations(
                        outcome.canonicalized,
                        cv_input["source_blocks"],
                        self.norm_map,
                    )
                    if coverage_violations:
                        validation_issues.append(
                            {
                                "error_type": "SemanticValidationError",
                                "error_details": coverage_violations,
                            }
                        )
                    evidence_errors = collect_payload_evidence_binding_errors(
                        outcome.canonicalized, cv_input["source_blocks"]
                    )
                    if evidence_errors:
                        validation_issues.append(
                            {
                                "error_type": "SourceBindingError",
                                "error_details": evidence_errors,
                            }
                        )
                    annotation: CVExtractionResult | None = None
                    try:
                        annotation = validate_schema(outcome.canonicalized)
                    except SchemaValidationError as exc:
                        validation_issues.append(
                            {
                                "error_type": "SchemaValidationError",
                                "error_details": exc.errors,
                            }
                        )
                        raw_match_violations = collect_raw_match_field_evidence_violations(
                            outcome.canonicalized
                        )
                        if raw_match_violations:
                            validation_issues.append(
                                {
                                    "error_type": "SemanticValidationError",
                                    "error_details": raw_match_violations,
                                }
                            )
                        raw_semantic_violations = collect_raw_semantic_violations(
                            outcome.canonicalized
                        )
                        if raw_semantic_violations:
                            validation_issues.append(
                                {
                                    "error_type": "SemanticValidationError",
                                    "error_details": raw_semantic_violations,
                                }
                            )
                    taxonomy_coverage_violations = (
                        collect_taxonomy_skill_coverage_violations(
                            annotation if annotation is not None else outcome.canonicalized,
                            self.norm_map,
                            cv_input["source_blocks"],
                        )
                    )
                    if taxonomy_coverage_violations:
                        validation_issues.append(
                            {
                                "error_type": "SemanticValidationError",
                                "error_details": taxonomy_coverage_violations,
                            }
                        )
                    if annotation is not None:
                        skill_evidence_violations = (
                            collect_skill_evidence_support_violations(
                                annotation,
                                self.norm_map,
                                cv_input["source_blocks"],
                            )
                        )
                        if skill_evidence_violations:
                            validation_issues.append(
                                {
                                    "error_type": "SemanticValidationError",
                                    "error_details": skill_evidence_violations,
                                }
                            )
                        for semantic_validator in (
                            validate_semantic_constraints,
                            lambda value: validate_skill_item_type_contract(value, self.norm_map),
                        ):
                            try:
                                semantic_validator(annotation)
                            except SemanticValidationError as exc:
                                validation_issues.append(
                                    {
                                        "error_type": "SemanticValidationError",
                                        "error_details": exc.violations,
                                    }
                                )
                    if validation_issues:
                        raise CandidateValidationError(
                            f"Candidate failed {len(validation_issues)} deterministic validation group(s)",
                            issues=validation_issues,
                        )
                    assert annotation is not None
                    outcome.stage = "provenance"
                    annotation = align_all_evidence(annotation, cv_input["source_blocks"])
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
                        validation_error.issues
                        if isinstance(validation_error, CandidateValidationError)
                        else validation_error.errors
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
                    repair_plan = (
                        None
                        if outcome.retry_mode == "local_repair"
                        else plan_local_repair(
                            outcome.canonicalized,
                            validation_error.__class__.__name__,
                            error_details,
                            cv_input["source_blocks"],
                        )
                    )
                    if repair_plan is not None:
                        repair_targets = [
                            (
                                {
                                    "singleton": target.collection,
                                    "current_value": result.data.get(target.collection),
                                }
                                if target.index is None
                                else {
                                    "collection": target.collection,
                                    "index": target.index,
                                    "current_value": result.data[target.collection][target.index],
                                }
                            )
                            for target in repair_plan.targets
                        ]
                        retry_prompt = build_local_repair_prompt(
                            validation_error.__class__.__name__,
                            error_details,
                            repair_targets,
                            list(repair_plan.source_blocks),
                            list(repair_plan.append_collections),
                            dict(repair_plan.required_append_counts),
                        )
                        next_retry_mode = "local_repair"
                    else:
                        retry_prompt = build_validation_retry_prompt(
                            cv_input,
                            taxonomy_requirements,
                            coverage_requirements,
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
                                cv_input,
                                taxonomy_requirements,
                                coverage_requirements,
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

    def _extract_section_shards(
        self,
        cv_input: dict[str, Any],
        taxonomy_requirements: list[dict[str, str]],
        coverage_requirements: list[dict[str, Any]],
        checkpoint_key: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[DeepSeekResult, tuple[dict[str, Any], ...]]:
        """Extract disjoint field groups concurrently, then merge without inference."""

        def extract_shard(
            shard_name: str,
            top_level_fields: tuple[str, ...],
        ) -> tuple[str, DeepSeekResult, dict[str, Any]]:
            system_prompt = build_system_prompt(top_level_fields)
            user_prompt = build_user_prompt(
                cv_input,
                taxonomy_requirements,
                coverage_requirements,
                top_level_fields=top_level_fields,
            )
            started = perf_counter()
            result = self.client.extract(system_prompt, user_prompt)
            elapsed_ms = round((perf_counter() - started) * 1000, 3)
            unexpected_fields = sorted(set(result.data) - set(top_level_fields))
            if unexpected_fields:
                raise InvalidJSONError(
                    f"Section shard '{shard_name}' returned unexpected fields: "
                    + ", ".join(unexpected_fields),
                    raw_response=result.raw_response,
                )
            return (
                shard_name,
                result,
                {
                    "attempt": 1,
                    "mode": "initial_section_shard",
                    "shard": shard_name,
                    "elapsed_ms": elapsed_ms,
                    "transport_attempt_count": result.transport_attempt_count,
                    "system_prompt_characters": len(system_prompt),
                    "user_prompt_characters": len(user_prompt),
                    "response_characters": len(result.raw_response),
                },
            )

        shard_results: list[tuple[str, DeepSeekResult, dict[str, Any]]] = []
        pending_shards: list[tuple[str, tuple[str, ...]]] = []
        for shard_name, fields in SECTION_EXTRACTION_SHARDS:
            cached = (
                self.checkpoint_store.load(
                    checkpoint_key,
                    f"section:{shard_name}",
                )
                if self.checkpoint_store is not None and checkpoint_key is not None
                else None
            )
            if cached is None:
                pending_shards.append((shard_name, fields))
                continue
            LOGGER.info(
                "cv_extraction_checkpoint_hit checkpoint=%s stage=section:%s",
                checkpoint_key[:12],
                shard_name,
            )
            attempt = dict(cached["attempt"])
            attempt["checkpoint_hit"] = True
            shard_results.append(
                (
                    shard_name,
                    DeepSeekResult(
                        data=cached["data"],
                        raw_response=cached["raw_response"],
                        transport_attempt_count=0,
                    ),
                    attempt,
                )
            )

        def publish_progress() -> None:
            if progress_callback is None:
                return
            completed = [name for name, _, _ in shard_results]
            progress_callback(
                {
                    "stage": "extracting_sections",
                    "completed_sections": completed,
                    "active_sections": [
                        name for name, _ in pending_shards if name not in completed
                    ],
                    "total_sections": len(SECTION_EXTRACTION_SHARDS),
                    "percent": 0.24
                    + 0.48 * len(completed) / len(SECTION_EXTRACTION_SHARDS),
                }
            )

        publish_progress()

        futures: dict[
            Future[tuple[str, DeepSeekResult, dict[str, Any]]], str
        ] = {}
        with ThreadPoolExecutor(
            max_workers=max(1, len(pending_shards)),
            thread_name_prefix="cv-section",
        ) as executor:
            for shard_name, fields in pending_shards:
                LOGGER.info(
                    "cv_extraction_stage_started checkpoint=%s stage=section:%s",
                    checkpoint_key[:12] if checkpoint_key else "disabled",
                    shard_name,
                )
                future = executor.submit(extract_shard, shard_name, fields)
                futures[future] = shard_name
            first_error: Exception | None = None
            for future in as_completed(futures):
                try:
                    shard_name, result, attempt = future.result()
                except Exception as exc:
                    failed_shard = futures[future]
                    LOGGER.exception(
                        "cv_extraction_stage_failed checkpoint=%s stage=section:%s error=%s",
                        checkpoint_key[:12] if checkpoint_key else "disabled",
                        failed_shard,
                        type(exc).__name__,
                    )
                    if first_error is None:
                        first_error = exc
                    continue
                if self.checkpoint_store is not None and checkpoint_key is not None:
                    self.checkpoint_store.save(
                        checkpoint_key,
                        f"section:{shard_name}",
                        {
                            "data": result.data,
                            "raw_response": result.raw_response,
                            "attempt": attempt,
                        },
                    )
                LOGGER.info(
                    "cv_extraction_stage_completed checkpoint=%s stage=section:%s duration_ms=%s",
                    checkpoint_key[:12] if checkpoint_key else "disabled",
                    shard_name,
                    attempt["elapsed_ms"],
                )
                shard_results.append((shard_name, result, attempt))
                publish_progress()
            if first_error is not None:
                raise first_error

        payload: dict[str, Any] = {}
        raw_responses: dict[str, str] = {}
        attempts: list[dict[str, Any]] = []
        transport_attempt_count = 0
        for shard_name, result, attempt in shard_results:
            payload.update(result.data)
            raw_responses[shard_name] = result.raw_response
            attempts.append(attempt)
            transport_attempt_count += result.transport_attempt_count

        # Missing optional collections have the same meaning as an empty result;
        # defaults are inserted before the existing whole-document validators run.
        for _, fields in SECTION_EXTRACTION_SHARDS:
            for field_name in fields:
                payload.setdefault(field_name, None if field_name == "personal_info" else [])
        raw_response = json.dumps(
            {"section_shards": raw_responses},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (
            DeepSeekResult(
                data=payload,
                raw_response=raw_response,
                transport_attempt_count=transport_attempt_count,
            ),
            tuple(attempts),
        )

    def extract_one(
        self,
        *,
        document_id: str,
        raw_text: str,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> CVSingleExtraction:
        """Run the existing deterministic pipeline for one HTTP document."""
        self._task_started = perf_counter()
        cv_input, failed_case = preprocess_row(
            {"cv_id": document_id, "cv_text": raw_text}, 1
        )
        if failed_case is not None or cv_input is None:
            raise InputFormatError(
                (failed_case or {}).get("error_message", "CV input is invalid")
            )
        taxonomy_requirements = collect_source_taxonomy_requirements(
            self.norm_map, cv_input["source_blocks"]
        )
        coverage_requirements = collect_source_coverage_requirements(
            cv_input["source_blocks"]
        )
        user_prompt = build_user_prompt(
            cv_input, taxonomy_requirements, coverage_requirements
        )
        initial_result: DeepSeekResult | None = None
        initial_api_attempts: tuple[dict[str, Any], ...] = ()
        checkpoint_key = (
            build_content_checkpoint_key(
                raw_text=raw_text,
                runtime_fingerprint=self.checkpoint_fingerprint,
            )
            if self.checkpoint_store is not None
            else None
        )
        if self.parallel_section_extraction:
            initial_result, initial_api_attempts = self._extract_section_shards(
                cv_input,
                taxonomy_requirements,
                coverage_requirements,
                checkpoint_key=checkpoint_key,
                progress_callback=progress_callback,
            )
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "semantic_validating",
                    "completed_sections": [name for name, _ in SECTION_EXTRACTION_SHARDS],
                    "active_sections": [],
                    "total_sections": len(SECTION_EXTRACTION_SHARDS),
                    "percent": 0.76,
                }
            )
        outcome = self._extract_validated(
            cv_input,
            user_prompt,
            initial_result=initial_result,
            initial_api_attempts=initial_api_attempts,
        )
        if outcome.error is not None:
            raise outcome.error
        if outcome.annotation is None:
            raise RuntimeError("Validated extraction completed without an annotation")
        annotation = deduplicate_extraction(outcome.annotation)
        review_flags = validate_business_rules(annotation)
        normalized = normalize_extraction(annotation, self.norm_map)
        review_flags.extend(validate_normalized_rules(normalized))
        return CVSingleExtraction(
            extraction=annotation,
            normalized=normalized,
            review_flags=tuple(review_flags),
            api_attempts=tuple(outcome.api_attempts),
            semantic_repair_count=outcome.validation_retry_count,
        )

    def run(self, input_xlsx: str, output_dir: str) -> None:
        """Run CV extraction on an Excel file containing resumes."""
        rows = load_excel_rows(input_xlsx)
        indexed_rows = list(enumerate(rows, start=1))
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
            "normalization_path": str(Path(self.normalization_path).resolve()),
            "continue_on_error": self.continue_on_error,
            "audit_sample_rate": self.audit_sample_rate,
            "max_workers": self.max_workers,
            "semantic_retry_attempts": self.semantic_retry_attempts,
            "api_timeout_seconds": self.api_timeout_seconds,
            "total_rows": len(indexed_rows),
            "selected_row_indices": [index for index, _ in indexed_rows],
            "extraction_schema_version": FIELD_CONTRACT_VERSION,
            "normalization_taxonomy_version": self.norm_map.get("version", "1.0"),
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
            api_timeout_seconds=self.api_timeout_seconds,
        )

        annotations: list[CVExtractionResult] = []
        normalized_results: list[CVNormalizedResult] = []
        all_review_flags: list[dict[str, Any]] = []
        failed_cases: list[dict[str, Any]] = []
        illegal_enum_cases: list[dict[str, Any]] = []
        api_call_count = 0
        transport_retry_count = 0
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

        executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="cv-api")
        prepared_cvs: list[dict[str, Any]] = []
        for row_index, row in indexed_rows:
            initial_cv_id = row.get("cv_id") or f"cv_{row_index:06d}"
            audit.log_event("cv_started", row_index=row_index, cv_id=initial_cv_id)
            prepared: dict[str, Any] = {
                "row_index": row_index,
                "row": row,
                "started_at": perf_counter(),
                "cv_input": None,
                "failed_case": None,
                "user_prompt": None,
                "future": None,
                "preprocess_error": None,
            }
            try:
                cv_input, failed_case = preprocess_row(row, row_index)
                prepared["cv_input"] = cv_input
                prepared["failed_case"] = failed_case
                if cv_input is not None:
                    taxonomy_requirements = collect_source_taxonomy_requirements(
                        self.norm_map, cv_input["source_blocks"]
                    )
                    coverage_requirements = collect_source_coverage_requirements(
                        cv_input["source_blocks"]
                    )
                    user_prompt = build_user_prompt(
                        cv_input, taxonomy_requirements, coverage_requirements
                    )
                    prepared["user_prompt"] = user_prompt
                    prepared["future"] = executor.submit(
                        self._extract_validated,
                        cv_input,
                        user_prompt,
                    )
            except Exception as exc:
                prepared["preprocess_error"] = exc
            prepared_cvs.append(prepared)

        for prepared in tqdm(prepared_cvs, desc="Processing CVs"):
            row_index = prepared["row_index"]
            row = prepared["row"]
            started_at = prepared["started_at"]
            stage = "preprocess"
            cv_id = row.get("cv_id") or f"cv_{row_index:06d}"
            cv_input: dict[str, Any] | None = None
            user_prompt: str | None = None
            raw_response: str | None = None
            extracted: dict[str, Any] | None = None
            canonicalized: dict[str, Any] | None = None
            review_flags: list[dict[str, Any]] = []
            final_annotation: CVExtractionResult | None = None
            extraction_attempts: list[dict[str, Any]] = []
            retry_mode = "initial"
            repair_response: str | None = None
            merged_repair_payload: dict[str, Any] | None = None
            deterministic_corrections: list[dict[str, Any]] = []
            try:
                if prepared["preprocess_error"] is not None:
                    raise prepared["preprocess_error"]
                cv_input = prepared["cv_input"]
                failed_case = prepared["failed_case"]
                if failed_case is not None:
                    failed_cases.append(failed_case)
                    audit.write_failed_record(failed_case["cv_id"], row_index, failed_case)
                    audit.log_event(
                        "row_failed",
                        row_index=row_index,
                        cv_id=failed_case["cv_id"],
                        stage=stage,
                        error_type=failed_case["error_type"],
                        elapsed_ms=round((perf_counter() - started_at) * 1000, 3),
                    )
                    audit.write_cv_audit(
                        failed_case["cv_id"],
                        row_index,
                        {
                            "audit_reason": "failed",
                            "stage": stage,
                            "source_row": row,
                            "error": failed_case,
                        },
                    )
                    continue
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
                transport_retry_count += sum(
                    max(0, int(api_attempt.get("transport_attempt_count", 0)) - 1)
                    for api_attempt in outcome.api_attempts
                )
                validation_retry_count += outcome.validation_retry_count
                local_repair_count += outcome.local_repair_count
                full_reextract_count += outcome.full_reextract_count
                local_repair_protocol_rejected_count += outcome.local_repair_protocol_rejected_count
                for event in outcome.retry_events:
                    event_payload = dict(event)
                    event_type = event_payload.pop("event_type")
                    audit.log_event(event_type, row_index=row_index, cv_id=cv_id, **event_payload)
                for api_attempt in outcome.api_attempts:
                    audit.log_event(
                        "api_attempt_finished",
                        row_index=row_index,
                        cv_id=cv_id,
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
                    cv_id=cv_id,
                    attempt_count=len(extraction_attempts),
                    elapsed_ms=round((perf_counter() - started_at) * 1000, 3),
                )

                stage = "deduplication"
                annotation = deduplicate_extraction(annotation)
                stage = "business_validation"
                review_flags = validate_business_rules(annotation)
                stage = "normalization"
                normalized = normalize_extraction(annotation, self.norm_map)
                review_flags.extend(validate_normalized_rules(normalized))
                final_annotation = annotation

                annotations.append(annotation)
                normalized_results.append(normalized)
                all_review_flags.extend(review_flags)
                audit.write_success_record(cv_id, row_index, annotation, review_flags)
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
                should_audit = has_deterministic_corrections or had_validation_retry or audit.should_audit_cv(
                    row_index, has_review_flags=has_review_flags, failed=False
                )
                audit.log_event(
                    "cv_succeeded",
                    row_index=row_index,
                    cv_id=cv_id,
                    review_flag_count=len(review_flags),
                    deterministic_correction_count=len(deterministic_corrections),
                    validation_retry_count=max(0, len(extraction_attempts) - 1),
                    audited=should_audit,
                    elapsed_ms=round((perf_counter() - started_at) * 1000, 3),
                )
                if should_audit:
                    audit.write_cv_audit(
                        cv_id,
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
                failed_cv_id = cv_input["cv_id"] if cv_input is not None else cv_id
                failed_case = {
                    "cv_id": failed_cv_id,
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
                        failed_cv_id,
                        row_index,
                    )
                    illegal_enum_cases.extend(row_illegal_enum_cases)
                    audit.write_illegal_enum_cases(row_illegal_enum_cases)
                    failed_case["illegal_enum_case_count"] = len(row_illegal_enum_cases)
                audit.log_event(
                    "cv_failed",
                    row_index=row_index,
                    cv_id=failed_cv_id,
                    stage=stage,
                    error_type=exc.__class__.__name__,
                    elapsed_ms=round((perf_counter() - started_at) * 1000, 3),
                )
                audit.write_cv_audit(
                    failed_cv_id,
                    row_index,
                    {
                        "audit_reason": "failed",
                        "stage": stage,
                        "user_prompt": user_prompt,
                        "raw_response": raw_response,
                        "raw_response_kind": retry_mode,
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
                    audit.write_failed_record(failed_cv_id, row_index, failed_case)
                    manifest.update(
                        {
                            "run_failed_at": utc_now_iso(),
                            "success_count": len(annotations),
                            "failed_count": len(failed_cases) + 1,
                            "review_flag_count": len(all_review_flags),
                            "api_call_count": api_call_count,
                            "transport_retry_count": transport_retry_count,
                            "validation_retry_count": validation_retry_count,
                            "local_repair_count": local_repair_count,
                            "full_reextract_count": full_reextract_count,
                            "local_repair_protocol_rejected_count": local_repair_protocol_rejected_count,
                            "local_repair_recovered_count": local_repair_recovered_count,
                            "deterministic_correction_count": deterministic_correction_count,
                            "failed_row_index": row_index,
                            "failed_cv_id": failed_cv_id,
                            "failed_stage": stage,
                            "failed_error_type": exc.__class__.__name__,
                        }
                    )
                    audit.write_manifest(manifest)
                    audit.log_event(
                        "run_aborted",
                        row_index=row_index,
                        cv_id=failed_cv_id,
                        stage=stage,
                        error_type=exc.__class__.__name__,
                    )
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise
                failed_cases.append(failed_case)
                audit.write_failed_record(failed_cv_id, row_index, failed_case)

        executor.shutdown(wait=True)
        final_dir = audit.final_dir
        match_feature_as_of_date = date.today()
        match_feature_profiles: list[CVMatchFeatureResult] = [
            build_cv_match_features(
                annotation,
                normalized,
                self.norm_map,
                as_of_date=match_feature_as_of_date,
            )
            for annotation, normalized in zip(annotations, normalized_results, strict=True)
        ]
        capability_verification_profiles: list[CVCapabilityVerificationResult] = [
            build_capability_verification(profile) for profile in match_feature_profiles
        ]
        export_jsonl(annotations, str(final_dir / "annotations.jsonl"))
        export_nested_json(annotations, str(final_dir / "annotations_nested.json"))
        classification_records, classification_summary = build_classification_records(
            iter_cv_skill_occurrences(normalized_results), self.skill_taxonomy
        )
        unified_normalized_results = write_unified_normalized_artifacts(
            normalized_results,
            classification_records,
            self.skill_taxonomy,
            final_dir,
        )
        export_match_features_jsonl(
            match_feature_profiles,
            str(final_dir / "match_features.jsonl"),
        )
        export_match_feature_profiles(
            match_feature_profiles,
            str(final_dir / "match_feature_profiles.json"),
        )
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
                "transport_retry_count": transport_retry_count,
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
                "match_feature_as_of_date": match_feature_as_of_date.isoformat(),
                "match_feature_derivation_version": MATCH_FEATURE_DERIVATION_VERSION,
                "match_feature_profile_count": len(match_feature_profiles),
                "match_feature_count": sum(
                    len(profile.features) for profile in match_feature_profiles
                ),
                "resolved_match_feature_count": sum(
                    feature.resolution_status == "resolved"
                    for profile in match_feature_profiles
                    for feature in profile.features
                ),
                "capability_verification_derivation_version": (
                    CAPABILITY_VERIFICATION_DERIVATION_VERSION
                ),
                "capability_verification_profile_count": len(
                    capability_verification_profiles
                ),
                "capability_profile_count": sum(
                    len(result.profiles)
                    for result in capability_verification_profiles
                ),
                "capability_evidence_link_count": sum(
                    len(result.evidence_links)
                    for result in capability_verification_profiles
                ),
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
            transport_retry_count=transport_retry_count,
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
