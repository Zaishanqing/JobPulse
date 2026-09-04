from __future__ import annotations

from pydantic import ValidationError

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1

from ..application.errors import ExtractionErrorCode


class EnvelopeItemValidationError(ValueError):
    """Typed HTTP-adapter failure raised before the application boundary."""

    def __init__(self, code: ExtractionErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


def parse_envelope_item(value: object) -> CrawlerJDEnvelopeV1:
    """Validate one raw request item at the API boundary."""
    try:
        return CrawlerJDEnvelopeV1.model_validate(value)
    except ValidationError as exc:
        raise EnvelopeItemValidationError(ExtractionErrorCode.INVALID_ENVELOPE) from exc
