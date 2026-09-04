class JDExtractorError(Exception):
    """Base exception for the JD extractor project."""


from jobgraph_contracts.deepseek import InvalidJSONError, MissingAPIKeyError  # noqa: E402


class SchemaValidationError(JDExtractorError):
    """Raised when model output fails Pydantic schema validation."""

    def __init__(self, message: str, errors: list[dict] | None = None):
        super().__init__(message)
        self.errors = errors or []


class BusinessValidationError(JDExtractorError):
    """Raised when annotation data violates a hard business rule."""


class SemanticValidationError(BusinessValidationError):
    """Raised when a schema-valid annotation violates deterministic semantic constraints."""

    def __init__(self, message: str, violations: list[dict] | None = None):
        super().__init__(message)
        self.violations = violations or []


class SourceBindingError(JDExtractorError):
    """Raised when a requirement references an unknown source block."""

    def __init__(self, message: str, details: dict | list[dict] | None = None):
        super().__init__(message)
        self.details = details or {"message": message}


class InputFormatError(JDExtractorError):
    """Raised when the input file or row structure is invalid."""
