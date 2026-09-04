"""File and OCR extraction adapters."""

from dataclasses import dataclass
from pathlib import Path

from app.integrations.base import IntegrationError, IntegrationInputError
from app.integrations.registry import get_integration_registry
from app.models.file_asset import FileAsset


@dataclass(frozen=True)
class ExtractionOutcome:
    status: str
    text: str
    provider: str
    error_code: str | None = None
    error_message: str | None = None


def extract_file_text(file_asset: FileAsset, *, use_ocr: bool) -> ExtractionOutcome:
    registry = get_integration_registry()
    adapter = registry.ocr if use_ocr else registry.document_parser
    provider = adapter.status().provider
    content = registry.file_storage.read(Path(file_asset.path).name)
    try:
        text = adapter.extract_text(content, file_asset.content_type or "application/octet-stream")
        if not text.strip():
            raise IntegrationInputError(
                "ocr" if use_ocr else "document_parser",
                provider,
                "No text was extracted from the uploaded file",
            )
    except IntegrationError as exc:
        return ExtractionOutcome(
            status="failed",
            text="",
            provider=provider,
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
    return ExtractionOutcome(status="completed", text=text, provider=provider)
