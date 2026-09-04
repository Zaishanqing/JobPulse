from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.extraction_bundle import (
    ExtractedJDBundleV1,
    ExtractedJDBundleV2,
    JDExtractionExecutionV2,
    validate_bundle_matches_envelope,
)
from jobgraph_contracts.position_classifier import (
    PositionClassifier,
    build_jd_position_profile,
)
from ..audit import build_run_id
from ..deduplicator import deduplicate_extraction
from ..exceptions import (
    BusinessValidationError,
    InvalidJSONError,
    InputFormatError,
    MissingAPIKeyError,
    SchemaValidationError,
    SemanticValidationError,
    SourceBindingError,
)
from ..normalizer import load_normalization_map, normalize_extraction
from ..skill_taxonomy import (
    build_taxonomy_projection,
    load_skill_taxonomy_snapshot,
    validate_snapshot_against_normalization_map,
)
from ..pipeline import JDExtractionPipeline
from ..prompt_builder import build_system_prompt, build_user_prompt
from ..requirement_graph import build_requirement_graph
from ..validator import validate_normalized_rules
from ..text_cleaning import clean_jd_text
from .errors import ExtractionErrorCode, JDExtractionApplicationError
from .identity import build_document_id
from .mappers import envelope_to_pipeline_input, to_contract_extraction, to_contract_normalized
from .model_client import JDModelClient
from .text_mapping import EvidenceMappingError, NFKCTextMap, remap_extraction_evidence


class JDExtractionApplicationService:
    """The sole public application entry point for extracting one JD envelope."""

    def __init__(
        self,
        *,
        model: str,
        normalization_path: str,
        skill_taxonomy_path: str = "config/skill_taxonomy_snapshot.json",
        position_taxonomy_path: str = "config/position_taxonomy_catalog.v3.json",
        position_classification_max_attempts: int = 3,
        position_classifier: PositionClassifier | None = None,
        client: JDModelClient | None = None,
        extraction_provider: str = "deepseek",
        extraction_run_id: str | None = None,
        semantic_retry_attempts: int = 2,
        prompt_version: str = "jd-prompt.v1",
        algorithm_version: str = "jd-llm-extraction.v1",
        normalization_version: str = "v2",
    ) -> None:
        self.model = model
        self.extraction_provider = extraction_provider
        self.extraction_run_id = extraction_run_id or build_run_id()
        self.prompt_version = prompt_version
        self.algorithm_version = algorithm_version
        self.normalization_version = normalization_version
        try:
            self._pipeline = self._build_pipeline(
                model,
                normalization_path,
                semantic_retry_attempts,
                client,
            )
            self._skill_taxonomy = load_skill_taxonomy_snapshot(
                skill_taxonomy_path
            )
            validate_snapshot_against_normalization_map(
                self._skill_taxonomy,
                self._pipeline.norm_map,
            )
            self._position_classifier = position_classifier or PositionClassifier(
                catalog_path=position_taxonomy_path,
                model=model,
                max_attempts=position_classification_max_attempts,
            )
        except MissingAPIKeyError as exc:
            raise JDExtractionApplicationError(
                ExtractionErrorCode.MODEL_UNAVAILABLE,
                "The configured model provider is unavailable.",
            ) from exc
        except InputFormatError as exc:
            raise JDExtractionApplicationError(
                ExtractionErrorCode.NORMALIZATION_FAILED,
                "The normalization configuration could not be loaded.",
            ) from exc
        except Exception as exc:
            raise JDExtractionApplicationError(
                ExtractionErrorCode.INTERNAL_ERROR,
                "The extraction service could not be initialized.",
            ) from exc

    @staticmethod
    def _build_pipeline(
        model: str,
        normalization_path: str,
        semantic_retry_attempts: int,
        client: JDModelClient | None,
    ) -> JDExtractionPipeline:
        if client is None:
            return JDExtractionPipeline(
                model=model,
                normalization_path=normalization_path,
                max_workers=1,
                semantic_retry_attempts=semantic_retry_attempts,
            )
        if semantic_retry_attempts < 0 or semantic_retry_attempts > 2:
            raise ValueError("semantic_retry_attempts must be between 0 and 2.")
        pipeline = JDExtractionPipeline.__new__(JDExtractionPipeline)
        pipeline.model = model
        pipeline.semantic_retry_attempts = semantic_retry_attempts
        pipeline.system_prompt = build_system_prompt()
        pipeline.client = client
        pipeline.norm_map = load_normalization_map(normalization_path)
        return pipeline

    def extract_one(self, envelope: CrawlerJDEnvelopeV1) -> ExtractedJDBundleV1:
        if not isinstance(envelope, CrawlerJDEnvelopeV1):
            raise JDExtractionApplicationError(
                ExtractionErrorCode.INVALID_ENVELOPE,
                "A validated CrawlerJDEnvelopeV1 is required.",
            )
        started_at = datetime.now(timezone.utc)
        document_id = build_document_id(
            envelope.source_platform,
            envelope.source_record_id,
            envelope.source_version,
        )
        jd_input = envelope_to_pipeline_input(envelope, document_id)
        cleaned_text = clean_jd_text(envelope.raw_text)
        try:
            text_map = NFKCTextMap(cleaned_text)
        except EvidenceMappingError as exc:
            raise JDExtractionApplicationError(
                ExtractionErrorCode.EVIDENCE_VALIDATION_FAILED,
                "The envelope raw text cannot be mapped to the Pipeline NFKC text.",
            ) from exc

        outcome = self._pipeline._extract_validated(jd_input, build_user_prompt(jd_input))
        if outcome.error is not None:
            self._raise_application_error(outcome.error, outcome.stage)
        if outcome.annotation is None:
            raise JDExtractionApplicationError(
                ExtractionErrorCode.INTERNAL_ERROR,
                "Extraction completed without a validated result.",
            )

        try:
            annotation = deduplicate_extraction(outcome.annotation)
            annotation.requirement_graph = build_requirement_graph(
                annotation, jd_input["source_blocks"]
            )
            normalized = normalize_extraction(annotation, self._pipeline.norm_map, jd_input["jd_text"])
            outcome.review_flags.extend(validate_normalized_rules(normalized))
        except Exception as exc:
            self._raise_application_error(exc, "normalization")

        try:
            raw_annotation = remap_extraction_evidence(annotation, text_map)
        except EvidenceMappingError as exc:
            raise JDExtractionApplicationError(
                ExtractionErrorCode.EVIDENCE_VALIDATION_FAILED,
                "Evidence could not be mapped exactly to the cleaned text.",
            ) from exc

        try:
            extraction_result = to_contract_extraction(raw_annotation)
            normalized_result = to_contract_normalized(normalized, raw_annotation)
            self._validate_raw_evidence(extraction_result, cleaned_text)
            bundle = ExtractedJDBundleV1(
                source_platform=envelope.source_platform,
                source_record_id=envelope.source_record_id,
                source_version=envelope.source_version,
                cleaned_text=cleaned_text,
                extraction_result=extraction_result,
                normalized_result=normalized_result,
                review_flags=outcome.review_flags,
                extraction_provider=self.extraction_provider,
                model_version=self.model,
                extraction_run_id=self.extraction_run_id,
                extraction_started_at=started_at,
                extraction_finished_at=datetime.now(timezone.utc),
            )
            validate_bundle_matches_envelope(envelope, bundle)
            return bundle
        except JDExtractionApplicationError:
            raise
        except Exception as exc:
            raise JDExtractionApplicationError(
                ExtractionErrorCode.CONTRACT_VALIDATION_FAILED,
                "Failed to construct a valid extraction bundle.",
            ) from exc

    def extract_one_v2(self, envelope: CrawlerJDEnvelopeV1) -> ExtractedJDBundleV2:
        bundle = self.extract_one(envelope)
        skill_ids = (
            skill.skill_id
            for requirement in bundle.normalized_result.normalized_requirements
            for skill in requirement.normalized_skills
            if skill.resolution_status == "resolved" and skill.skill_id is not None
        )
        skill_taxonomy = build_taxonomy_projection(
            skill_ids,
            self._skill_taxonomy,
        )
        extraction_payload = bundle.extraction_result.model_dump(mode="json")
        normalized_payload = bundle.normalized_result.model_dump(mode="json")
        profile = build_jd_position_profile(
            extraction_payload,
            normalized_payload,
        )
        domain_codes = []
        for skill in skill_taxonomy["skills"]:
            for relation in skill["classifications"]:
                if (
                    relation["facet"] == "domain"
                    and relation["code"] not in domain_codes
                ):
                    domain_codes.append(relation["code"])
        profile["skill_domains"] = domain_codes[:8]
        try:
            decision = self._position_classifier.classify([profile])[
                profile["document_id"]
            ]
            classification = self._position_classifier.materialize(
                decision,
                source_title=profile["title"],
            )
        except Exception as exc:
            self._raise_application_error(exc, "position_classification")
        payload = bundle.model_dump(mode="python")
        payload["schema_version"] = "extracted-jd-bundle-v2"
        payload["normalized_result"]["job_classification"] = classification
        flags = [
            flag
            for flag in payload.get("review_flags", [])
            if flag.get("issue_type") != "job_classification_not_resolved"
        ]
        if classification["classification_status"] not in {
            "resolved",
            "manually_confirmed",
        }:
            flags.append(
                {
                    "jd_id": profile["document_id"],
                    "requirement_id": "",
                    "item_id": "",
                    "issue_type": "job_classification_not_resolved",
                    "severity": "blocking",
                    "rule_scope": "document",
                    "issue_description": "岗位分类未达到发布状态",
                    "raw_text": "岗位分类未达到发布状态",
                    "suggested_action": "Review and confirm the standard position.",
                    "classification_status": classification[
                        "classification_status"
                    ],
                    "position_code": classification["position_code"],
                    "review_reason_codes": classification[
                        "review_reason_codes"
                    ],
                }
            )
        payload["review_flags"] = flags
        payload["skill_taxonomy"] = skill_taxonomy
        payload["execution"] = JDExtractionExecutionV2(
            mode="llm",
            provider=self.extraction_provider,
            model=self.model,
            prompt_version=self.prompt_version,
            algorithm_version=self.algorithm_version,
            schema_version="extracted-jd-bundle-v2",
            normalization_version=self.normalization_version,
            started_at=bundle.extraction_started_at,
            finished_at=bundle.extraction_finished_at,
        )
        result = ExtractedJDBundleV2.model_validate(payload)
        validate_bundle_matches_envelope(envelope, result)
        return result

    @staticmethod
    def _validate_raw_evidence(result: Any, raw_text: str) -> None:
        objects = []
        if result.job_title is not None:
            objects.append(result.job_title)
        objects.extend(result.responsibilities)
        objects.extend(result.requirements)
        objects.extend(result.company_facts)
        objects.extend(result.employment_facts)
        if any(
            not (
                item.evidence.alignment == "exact"
                and item.evidence.start is not None
                and item.evidence.end is not None
                and 0 <= item.evidence.start <= item.evidence.end <= len(raw_text)
                and raw_text[item.evidence.start : item.evidence.end] == item.evidence.quote
            )
            for item in objects
        ):
            raise JDExtractionApplicationError(
                ExtractionErrorCode.EVIDENCE_VALIDATION_FAILED,
                "Evidence does not point to an exact span of the envelope raw text.",
            )

    @staticmethod
    def _raise_application_error(exc: BaseException, stage: str) -> None:
        try:
            from openai import APIConnectionError, APITimeoutError, RateLimitError
        except ImportError:  # pragma: no cover - openai is a production dependency
            APIConnectionError = RateLimitError = ConnectionError
            APITimeoutError = TimeoutError
        code = ExtractionErrorCode.INTERNAL_ERROR
        message = "Extraction failed unexpectedly."
        if isinstance(exc, (TimeoutError, APITimeoutError)):
            code, message = ExtractionErrorCode.MODEL_TIMEOUT, "The model request timed out."
        elif isinstance(exc, (ConnectionError, MissingAPIKeyError, APIConnectionError, RateLimitError)):
            code, message = (
                ExtractionErrorCode.MODEL_UNAVAILABLE,
                "The configured model provider is unavailable.",
            )
        elif isinstance(exc, InvalidJSONError):
            code, message = (
                ExtractionErrorCode.MODEL_INVALID_RESPONSE,
                "The model returned an invalid response.",
            )
        elif isinstance(exc, SchemaValidationError):
            code, message = (
                ExtractionErrorCode.SCHEMA_VALIDATION_FAILED,
                "The model response failed schema validation.",
            )
        elif isinstance(exc, SourceBindingError):
            code, message = (
                ExtractionErrorCode.EVIDENCE_VALIDATION_FAILED,
                "The model response failed evidence validation.",
            )
        elif isinstance(exc, SemanticValidationError):
            code, message = (
                ExtractionErrorCode.SEMANTIC_VALIDATION_FAILED,
                "The model response failed semantic validation.",
            )
        elif isinstance(exc, BusinessValidationError):
            code, message = (
                ExtractionErrorCode.BUSINESS_VALIDATION_FAILED,
                "The model response failed business validation.",
            )
        elif stage == "normalization":
            code, message = (
                ExtractionErrorCode.NORMALIZATION_FAILED,
                "The validated extraction could not be normalized.",
            )
        raise JDExtractionApplicationError(code, message) from exc
