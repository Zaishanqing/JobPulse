from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePath
from typing import Any

from app.contexts.cv_ingestion.domain import (
    CV_EXTRACTION_METHODS,
    CV_QUALITY_FLAG_DIRECT_TEXT,
    CV_QUALITY_FLAG_NONE,
    CV_QUALITY_FLAG_OCR,
    CVDocumentTextExtraction,
    CVFileInputError,
)
from app.contexts.platform import FileRecord, FileUploadWorkflowPort
from app.domain.accounts import AccountActor
from app.integrations.base import IntegrationError
from app.integrations.contracts import FileStorage, OCRProvider


CV_FILE_TYPES = {
    "application/pdf": frozenset({".pdf"}),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": frozenset(
        {".docx"}
    ),
    "text/plain": frozenset({".txt"}),
    "image/png": frozenset({".png"}),
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
}


class CVFileTextExtractionAdapter:
    def __init__(
        self,
        files: FileUploadWorkflowPort,
        storage: FileStorage,
        pdf_parser: Any,
        docx_parser: Any,
        ocr: OCRProvider,
        maximum_size: Callable[[], int],
    ) -> None:
        self._files = files
        self._storage = storage
        self._pdf_parser = pdf_parser
        self._docx_parser = docx_parser
        self._ocr = ocr
        self._maximum_size = maximum_size

    def extract(
        self,
        actor: AccountActor,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
        use_ocr: bool,
    ) -> CVDocumentTextExtraction:
        safe_name = PurePath(filename).name
        suffix = PurePath(safe_name).suffix.lower()
        if content_type not in CV_FILE_TYPES:
            raise CVFileInputError(
                "CV_FILE_TYPE_UNSUPPORTED", "CV file content type is unsupported"
            )
        if suffix not in CV_FILE_TYPES[content_type]:
            raise CVFileInputError(
                "CV_FILE_MIME_MISMATCH", "CV file extension conflicts with content type"
            )
        if not content:
            raise CVFileInputError("CV_TEXT_EMPTY", "CV file is empty")
        if len(content) > self._maximum_size():
            raise CVFileInputError("CV_FILE_TOO_LARGE", "CV file exceeds configured size limit")
        record = self._files.upload(
            actor,
            filename=safe_name,
            content_type=content_type,
            content=content,
            purpose="cv_source_file",
        )
        try:
            stored = self._storage.read(record.storage_key)
            return self._extract_stored(record, stored, content_type, use_ocr)
        except Exception as exc:
            try:
                self._files.delete(actor, record.file_id)
            except Exception:
                pass
            raise self._map_error(exc) from exc

    def _extract_stored(
        self,
        record: FileRecord,
        content: bytes,
        content_type: str,
        use_ocr: bool,
    ) -> CVDocumentTextExtraction:
        if use_ocr:
            (
                text,
                page_count,
                provider,
                provider_version,
                method,
                quality_flags,
                ocr_layout,
            ) = self._extract_with_ocr(content, content_type)
        elif content_type == "application/pdf":
            text, page_count = self._pdf_parser.extract_text(content, content_type)
            if text.strip():
                provider = self._pdf_parser.status().provider
                provider_version = self._pdf_parser.status().version
                method = "pdf_text"
                quality_flags = (CV_QUALITY_FLAG_NONE,)
                ocr_layout = None
            else:
                # Scanned PDFs are valid PDFs with no copyable text layer. Fall
                # back internally so callers do not need to identify them first.
                (
                    text,
                    page_count,
                    provider,
                    provider_version,
                    method,
                    quality_flags,
                    ocr_layout,
                ) = self._extract_with_ocr(content, content_type)
        elif (
            content_type
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):
            text = self._docx_parser.extract_text(content, content_type)
            page_count = 0
            provider = self._docx_parser.status().provider
            provider_version = self._docx_parser.status().version
            method = "docx_text"
            quality_flags = (CV_QUALITY_FLAG_NONE,)
            ocr_layout = None
        elif content_type == "text/plain":
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CVFileInputError(
                    "CV_TEXT_EMPTY", "Plain text CV is not valid UTF-8"
                ) from exc
            page_count = 0
            provider = "direct_text"
            provider_version = None
            method = "direct_text"
            quality_flags = (CV_QUALITY_FLAG_DIRECT_TEXT,)
            ocr_layout = None
        else:
            raise CVFileInputError(
                "CV_FILE_TYPE_UNSUPPORTED", "CV file content type is unsupported"
            )
        if method not in CV_EXTRACTION_METHODS:
            raise CVFileInputError("CV_FILE_TYPE_UNSUPPORTED", "CV extraction method is invalid")
        if not text.strip():
            raise CVFileInputError("CV_TEXT_EMPTY", "CV file produced no text")
        raw_text = text.strip()
        return CVDocumentTextExtraction(
            source_file_id=record.file_id,
            original_filename=record.filename,
            content_type=record.content_type or content_type,
            extraction_method=method,
            extraction_provider=provider,
            extraction_provider_version=provider_version,
            extraction_status="completed",
            raw_text=raw_text,
            page_count=page_count,
            character_count=len(raw_text),
            quality_flags=quality_flags,
            ocr_layout=ocr_layout,
        )

    def _extract_with_ocr(
        self, content: bytes, content_type: str
    ) -> tuple[
        str,
        int,
        str,
        str | None,
        str,
        tuple[str, ...],
        tuple[Any, ...] | None,
    ]:
        extract_document = getattr(self._ocr, "extract_document", None)
        if callable(extract_document):
            text, raw_layout = extract_document(content, content_type)
            from app.domain.json_types import freeze_json_object

            ocr_layout = tuple(
                freeze_json_object(item, field="cv_ocr_layout") for item in raw_layout
            )
        else:
            text = self._ocr.extract_text(content, content_type)
            ocr_layout = None
        status = self._ocr.status()
        method = (
            "ocr_image"
            if content_type in {"image/png", "image/jpeg"}
            else "ocr_pdf"
        )
        return (
            text,
            self._pdf_page_count(content, content_type),
            status.provider,
            status.version,
            method,
            (CV_QUALITY_FLAG_OCR,),
            ocr_layout,
        )

    @staticmethod
    def _pdf_page_count(content: bytes, content_type: str) -> int:
        if content_type != "application/pdf":
            return 1
        try:
            import fitz

            document = fitz.open(stream=content, filetype="pdf")
            count = document.page_count
            document.close()
            return count
        except Exception:
            return 0

    @staticmethod
    def _map_error(exc: Exception) -> CVFileInputError:
        if isinstance(exc, CVFileInputError):
            return exc
        if isinstance(exc, IntegrationError) and exc.code:
            return CVFileInputError(exc.code, str(exc))
        if isinstance(exc, IntegrationError):
            if exc.capability == "ocr":
                return CVFileInputError("CV_OCR_FAILED", str(exc))
            if exc.capability == "document_parser":
                return CVFileInputError("CV_PDF_TEXT_EXTRACTION_FAILED", str(exc))
        return CVFileInputError("CV_FILE_TYPE_UNSUPPORTED", str(exc))
