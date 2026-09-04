class CVExtractorError(Exception):
    """Base exception for the CV extractor project."""
    code: str = "CV_EXTRACTION_CONTRACT_INVALID"


from jobgraph_contracts.deepseek import InvalidJSONError, MissingAPIKeyError  # noqa: E402


class SchemaValidationError(CVExtractorError):
    """Raised when model output fails Pydantic schema validation."""

    def __init__(self, message: str, errors: list[dict] | None = None):
        super().__init__(message)
        self.errors = errors or []


class BusinessValidationError(CVExtractorError):
    """Raised when annotation data violates a hard business rule."""


class SemanticValidationError(BusinessValidationError):
    """Raised when a schema-valid annotation violates deterministic semantic constraints."""

    def __init__(self, message: str, violations: list[dict] | None = None):
        super().__init__(message)
        self.violations = violations or []


class CandidateValidationError(CVExtractorError):
    """Raised with every deterministically discoverable issue in one candidate."""

    def __init__(self, message: str, issues: list[dict] | None = None):
        super().__init__(message)
        self.issues = issues or []


class SourceBindingError(CVExtractorError):
    """Raised when an entry references an unknown source block."""

    def __init__(self, message: str, details: dict | list[dict] | None = None):
        super().__init__(message)
        self.details = details or {"message": message}


class InputFormatError(CVExtractorError):
    """Raised when the input file or document structure is invalid."""


class EvidenceAlignmentError(CVExtractorError):
    code = "CV_EVIDENCE_ALIGNMENT_INVALID"
