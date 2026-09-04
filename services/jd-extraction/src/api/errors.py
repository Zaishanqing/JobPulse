from __future__ import annotations

from dataclasses import dataclass

from ..application.errors import ExtractionErrorCode, JDExtractionApplicationError


@dataclass(frozen=True)
class HTTPErrorSpec:
    status_code: int
    retryable: bool


_ERROR_MAP = {
    ExtractionErrorCode.INVALID_ENVELOPE: HTTPErrorSpec(422, False),
    ExtractionErrorCode.MODEL_UNAVAILABLE: HTTPErrorSpec(503, True),
    ExtractionErrorCode.MODEL_TIMEOUT: HTTPErrorSpec(504, True),
    ExtractionErrorCode.MODEL_INVALID_RESPONSE: HTTPErrorSpec(502, True),
    ExtractionErrorCode.SCHEMA_VALIDATION_FAILED: HTTPErrorSpec(422, False),
    ExtractionErrorCode.EVIDENCE_VALIDATION_FAILED: HTTPErrorSpec(422, False),
    ExtractionErrorCode.SEMANTIC_VALIDATION_FAILED: HTTPErrorSpec(422, False),
    ExtractionErrorCode.BUSINESS_VALIDATION_FAILED: HTTPErrorSpec(422, False),
    ExtractionErrorCode.NORMALIZATION_FAILED: HTTPErrorSpec(500, False),
    ExtractionErrorCode.CONTRACT_VALIDATION_FAILED: HTTPErrorSpec(500, False),
    ExtractionErrorCode.INTERNAL_ERROR: HTTPErrorSpec(500, False),
}


class APIError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        error_code: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.retryable = retryable


def application_error_spec(error: JDExtractionApplicationError) -> HTTPErrorSpec:
    return _ERROR_MAP.get(error.code, HTTPErrorSpec(500, False))
