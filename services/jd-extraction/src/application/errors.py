from __future__ import annotations

from enum import StrEnum


class ExtractionErrorCode(StrEnum):
    INVALID_ENVELOPE = "invalid_envelope"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_TIMEOUT = "model_timeout"
    MODEL_INVALID_RESPONSE = "model_invalid_response"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    EVIDENCE_VALIDATION_FAILED = "evidence_validation_failed"
    SEMANTIC_VALIDATION_FAILED = "semantic_validation_failed"
    BUSINESS_VALIDATION_FAILED = "business_validation_failed"
    NORMALIZATION_FAILED = "normalization_failed"
    CONTRACT_VALIDATION_FAILED = "contract_validation_failed"
    INTERNAL_ERROR = "internal_error"


class JDExtractionApplicationError(Exception):
    """Safe, stable error exposed by the single-JD application boundary."""

    def __init__(self, code: ExtractionErrorCode, message: str):
        super().__init__(message)
        self.code = code
