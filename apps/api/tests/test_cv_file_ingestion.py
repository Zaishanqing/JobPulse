from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.contexts.cv_ingestion import (
    CVDocumentTextExtraction,
    CVFileInputError,
    CVIngestionUseCases,
)
from app.contexts.cv_ingestion.domain import (
    CV_QUALITY_FLAG_OCR,
)
from app.contexts.data_validation import CVValidationPolicy, CVValidatorSet
from app.contexts.data_validation.fakes import FakeSkillCatalogResolutionPort
from app.contexts.platform import FileRecord
from app.domain.accounts import AccountActor
from app.infrastructure.cv_file_extraction import CVFileTextExtractionAdapter
from app.infrastructure.cv_ingestion import (
    ApplicationResumeImporter,
    SqlAlchemyCVIngestionUnitOfWork,
)
from app.integrations.base import CapabilityStatus, IntegrationUnavailableError
from app.integrations.local import DocxTextDocumentParser, PdfTextDocumentParser
from app.main import app
from app.models.resume import Resume
from app.models.outbox_message import OutboxMessage
from app.models.source_cv import CVExtractionTask, SourceCV, SourceCVVersion, ValidatedCVSnapshot
from app.domain.resumes import ResumeRuleViolation
from tests.runtime_database import SessionLocal, reset_database_data
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


class FakeCVProvider:
    request_id = "file-cv-provider-request"

    def extract(self, *, document_id: str, raw_text: str, progress_callback=None):
        return None


class FakeFileInput:
    def __init__(self, extraction: CVDocumentTextExtraction | None = None, error: CVFileInputError | None = None):
        self.extraction = extraction
        self.error = error
        self.calls = []

    def extract(self, actor, *, filename, content_type, content, use_ocr):
        self.calls.append((filename, content_type, use_ocr))
        if self.error is not None:
            raise self.error
        return self.extraction


def _extraction(*, raw_text: str = "熟练使用 Python", method: str = "pdf_text", source_file_id: str = "file-1") -> CVDocumentTextExtraction:
    return CVDocumentTextExtraction(
        source_file_id=source_file_id,
        original_filename="resume.pdf",
        content_type="application/pdf",
        extraction_method=method,
        extraction_provider="pymupdf",
        extraction_provider_version="1.0",
        extraction_status="completed",
        raw_text=raw_text,
        page_count=2,
        character_count=len(raw_text),
        quality_flags=("none",),
    )


def _actor(username: str, role: str = "personal_user") -> AccountActor:
    account_id = create_internal_user(username, role)
    assert account_id is not None
    return AccountActor(account_id, role)


def _use_cases(file_input) -> CVIngestionUseCases:
    return CVIngestionUseCases(
        lambda: SqlAlchemyCVIngestionUnitOfWork(SessionLocal),
        FakeCVProvider(),
        ApplicationResumeImporter(app.state.container.resumes),
        CVValidatorSet(CVValidationPolicy(), FakeSkillCatalogResolutionPort),
        file_input=file_input,
        enabled=True,
        max_attempts=3,
    )


def test_upload_and_schedule_persists_lineage_and_is_idempotent():
    actor = _actor("cv_file_owner_1")
    use_cases = _use_cases(FakeFileInput(_extraction()))
    first = use_cases.upload_and_schedule(
        actor,
        filename="resume.pdf",
        content_type="application/pdf",
        content=b"%PDF",
        use_ocr=False,
    )
    second = use_cases.upload_and_schedule(
        actor,
        filename="resume.pdf",
        content_type="application/pdf",
        content=b"%PDF",
        use_ocr=False,
    )
    assert first.source_cv_id == second.source_cv_id
    assert first.source_cv_version_id == second.source_cv_version_id
    assert first.cv_extraction_task_id == second.cv_extraction_task_id
    assert first.created_version is True
    assert second.created_version is False
    assert first.text_extraction_status == "completed"
    assert first.extraction_method == "pdf_text"
    with SessionLocal() as session:
        version = session.get(SourceCVVersion, first.source_cv_version_id)
        assert version.raw_text == "熟练使用 Python"
        assert version.page_count == 2
        assert version.quality_flags == ["none"]
        assert session.query(SourceCV).count() == 1
        assert session.query(CVExtractionTask).count() == 1
        assert session.query(Resume).count() == 0
        assert session.query(ValidatedCVSnapshot).count() == 0
        assert session.query(OutboxMessage).count() == 0


def test_upload_rejects_enterprise_user_and_accepts_personal_user():
    personal = _actor("cv_file_personal", "personal_user")
    enterprise = _actor("cv_file_enterprise", "enterprise_user")
    use_cases = _use_cases(FakeFileInput(_extraction()))
    result = use_cases.upload_and_schedule(
        personal,
        filename="resume.pdf",
        content_type="application/pdf",
        content=b"%PDF",
        use_ocr=False,
    )
    assert result.cv_extraction_task_id
    with pytest.raises(ResumeRuleViolation):
        use_cases.upload_and_schedule(
            enterprise,
            filename="resume.pdf",
            content_type="application/pdf",
            content=b"%PDF",
            use_ocr=False,
        )


def test_failed_file_extraction_creates_no_task_or_source():
    actor = _actor("cv_file_failure")
    error = CVFileInputError("CV_PDF_TEXT_EXTRACTION_FAILED", "bad pdf")
    use_cases = _use_cases(FakeFileInput(error=error))
    with pytest.raises(CVFileInputError) as exc:
        use_cases.upload_and_schedule(
            actor,
            filename="resume.pdf",
            content_type="application/pdf",
            content=b"%PDF",
            use_ocr=False,
        )
    assert exc.value.code == "CV_PDF_TEXT_EXTRACTION_FAILED"
    with SessionLocal() as session:
        assert session.query(SourceCV).count() == 0
        assert session.query(CVExtractionTask).count() == 0
        assert session.query(Resume).count() == 0


def _valid_pdf() -> bytes:
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Python developer")
    return document.tobytes()


def _valid_docx() -> bytes:
    from docx import Document
    from io import BytesIO

    document = Document()
    document.add_paragraph("Python developer")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_pdf_text_parser_extracts_copyable_text():
    parser = PdfTextDocumentParser()
    text, page_count = parser.extract_text(_valid_pdf(), "application/pdf")
    assert "Python developer" in text
    assert page_count >= 1


def test_pdf_text_parser_reports_concise_engine_version():
    import fitz

    status = PdfTextDocumentParser().status()

    assert status.version == str(fitz.VersionBind)
    assert len(status.version) <= 64


def test_docx_parser_extracts_text():
    parser = DocxTextDocumentParser()
    text = parser.extract_text(
        _valid_docx(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert "Python developer" in text


class FakeBlobStorage:
    def __init__(self):
        self.items = {}

    def save(self, key, content):
        self.items[key] = content
        return key

    def read(self, key):
        return self.items[key]

    def delete(self, key):
        self.items.pop(key, None)


class FakeFiles:
    def __init__(self, storage):
        self.records = {}
        self._index = 0
        self._storage = storage

    def upload(self, actor, *, filename, content_type, content, purpose):
        self._index += 1
        storage_key = f"key-{self._index}"
        self._storage.save(storage_key, content)
        record = FileRecord(
            f"file-{self._index}",
            actor.account_id,
            filename,
            content_type,
            storage_key,
            len(content),
            purpose,
            datetime.now(timezone.utc),
        )
        self.records[record.file_id] = record
        return record

    def delete(self, actor, file_id):
        self.records.pop(file_id, None)


class FakeOCR:
    def status(self):
        return CapabilityStatus(
            "ocr", "fake_ocr", "fake_cpu", True, False, "fake", version="1.0"
        )

    def extract_text(self, content, content_type):
        return "OCR recognized text"


def _adapter(ocr=None, pdf_parser=None):
    storage = FakeBlobStorage()
    files = FakeFiles(storage)
    adapter = CVFileTextExtractionAdapter(
        files,
        storage,
        pdf_parser or PdfTextDocumentParser(),
        DocxTextDocumentParser(),
        ocr or FakeOCR(),
        lambda: 1024 * 1024,
    )
    adapter._storage = storage
    adapter._files = files
    return adapter, storage, files


def test_adapter_routes_image_and_pdf_to_ocr():
    adapter, storage, files = _adapter()
    actor = _actor("cv_file_ocr")
    image = adapter.extract(
        actor,
        filename="resume.png",
        content_type="image/png",
        content=b"image",
        use_ocr=True,
    )
    assert image.extraction_method == "ocr_image"
    assert CV_QUALITY_FLAG_OCR in image.quality_flags
    pdf = adapter.extract(
        actor,
        filename="resume.pdf",
        content_type="application/pdf",
        content=_valid_pdf(),
        use_ocr=True,
    )
    assert pdf.extraction_method == "ocr_pdf"
    assert pdf.raw_text
    assert pdf.source_file_id
    assert len(storage.items) == 2


def test_adapter_automatically_ocr_scanned_pdf_without_text_layer():
    class EmptyPdfTextParser:
        def extract_text(self, content, content_type):
            return "", 1

    adapter, _, _ = _adapter(pdf_parser=EmptyPdfTextParser())
    result = adapter.extract(
        _actor("cv_file_scanned_pdf"),
        filename="resume.pdf",
        content_type="application/pdf",
        content=_valid_pdf(),
        use_ocr=False,
    )

    assert result.extraction_method == "ocr_pdf"
    assert result.extraction_provider == "fake_ocr"
    assert result.raw_text == "OCR recognized text"
    assert CV_QUALITY_FLAG_OCR in result.quality_flags


def test_adapter_maps_ocr_unavailable_to_stable_code():
    class UnavailableOCR:
        def status(self):
            return CapabilityStatus("ocr", "tesseract", "unavailable", False, False, "missing", version=None)

        def extract_text(self, content, content_type):
            raise IntegrationUnavailableError(
                "ocr", "tesseract", "Tesseract missing", code="CV_OCR_UNAVAILABLE"
            )

    adapter, _, _ = _adapter(UnavailableOCR())
    actor = _actor("cv_file_ocr_missing")
    with pytest.raises(CVFileInputError) as exc:
        adapter.extract(
            actor,
            filename="resume.png",
            content_type="image/png",
            content=b"image",
            use_ocr=True,
        )
    assert exc.value.code == "CV_OCR_UNAVAILABLE"


@pytest.mark.parametrize(
    "filename,content_type,expected",
    [
        ("resume.pdf", "text/plain", "CV_FILE_MIME_MISMATCH"),
        ("resume.bin", "application/pdf", "CV_FILE_MIME_MISMATCH"),
        ("resume.pdf", "application/octet-stream", "CV_FILE_TYPE_UNSUPPORTED"),
    ],
)
def test_adapter_rejects_unsupported_and_mismatched_files(filename, content_type, expected):
    adapter, _, _ = _adapter()
    actor = _actor(f"cv_file_bad_{len(content_type)}")
    with pytest.raises(CVFileInputError) as exc:
        adapter.extract(
            actor,
            filename=filename,
            content_type=content_type,
            content=b"data",
            use_ocr=False,
        )
    assert exc.value.code == expected


def test_adapter_rejects_empty_and_oversize_files():
    adapter, _, _ = _adapter()
    actor = _actor("cv_file_limits")
    with pytest.raises(CVFileInputError) as exc:
        adapter.extract(
            actor,
            filename="empty.pdf",
            content_type="application/pdf",
            content=b"",
            use_ocr=False,
        )
    assert exc.value.code == "CV_TEXT_EMPTY"
    with pytest.raises(CVFileInputError) as exc:
        adapter.extract(
            actor,
            filename="large.pdf",
            content_type="application/pdf",
            content=b"x" * (1024 * 1024 + 1),
            use_ocr=False,
        )
    assert exc.value.code == "CV_FILE_TOO_LARGE"


def test_corrupt_pdf_and_docx_map_to_stable_codes():
    adapter, _, _ = _adapter()
    actor = _actor("cv_file_corrupt")
    with pytest.raises(CVFileInputError) as exc:
        adapter.extract(
            actor,
            filename="bad.pdf",
            content_type="application/pdf",
            content=b"%PDF-not-valid",
            use_ocr=False,
        )
    assert exc.value.code == "CV_PDF_TEXT_EXTRACTION_FAILED"
    with pytest.raises(CVFileInputError) as exc:
        adapter.extract(
            actor,
            filename="bad.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            content=b"not-docx",
            use_ocr=False,
        )
    assert exc.value.code == "CV_DOCX_TEXT_EXTRACTION_FAILED"


def test_upload_api_accepts_personal_and_rejects_enterprise():
    _actor("cv_api_personal", "personal_user")
    _actor("cv_api_enterprise", "enterprise_user")
    use_cases = _use_cases(FakeFileInput(_extraction(source_file_id="file-api")))
    original = app.state.container
    app.state.container = replace(original, cv_ingestion=use_cases)
    try:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "cv_api_personal", "password": "password123"},
        )
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        response = client.post(
            "/api/v1/source-cvs/upload-and-extract",
            files={"file": ("resume.pdf", b"%PDF", "application/pdf")},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["data"]["text_extraction_status"] == "completed"

        login = client.post(
            "/api/v1/auth/login",
            json={"username": "cv_api_enterprise", "password": "password123"},
        )
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        denied = client.post(
            "/api/v1/source-cvs/upload-and-extract",
            files={"file": ("resume.pdf", b"%PDF", "application/pdf")},
            headers=headers,
        )
        assert denied.status_code == 403
    finally:
        app.state.container = original
