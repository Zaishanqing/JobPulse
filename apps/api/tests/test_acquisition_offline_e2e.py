from __future__ import annotations

import pytest

from app.contexts.extraction_tasks import ExtractionTaskUseCases
from app.infrastructure.extraction_tasks import SqlAlchemyExtractionTaskUnitOfWork
from app.models.offline_import import OfflineImportBatch
from app.models.jd import JobDescription
from app.models.source_jd import SourceJD, SourceJDVersion
from app.offline_import.importer import OfflineBundleImporter
from app.offline_import.repository import OfflineImportRepository
from tests.offline_bundle_test_support import envelope, make_bundle
from tests.runtime_database import SessionLocal, reset_database_data


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def test_deterministic_bundle_imports_through_existing_path_to_source_jd(tmp_path):
    path = make_bundle(
        tmp_path / "acquisition-e2e.zip",
        bundle_id="bundle-acquisition-e2e",
        envelopes=[envelope("one", "first"), envelope("two", "second")],
    )
    use_cases = ExtractionTaskUseCases(
        lambda: SqlAlchemyExtractionTaskUnitOfWork(SessionLocal),
        {},
        3,
    )
    importer = OfflineBundleImporter(
        OfflineImportRepository(SessionLocal),
        use_cases.import_crawler_envelope_as_jd,
        "llm",
    )

    summary = importer.import_bundle(path)

    assert summary.imported_count == 2
    assert summary.failed_count == 0
    with SessionLocal() as session:
        assert session.query(SourceJD).count() == 2
        assert session.query(SourceJDVersion).count() == 2
        assert session.query(JobDescription).count() == 2
        assert {
            jd.parse_status for jd in session.query(JobDescription).all()
        } == {"pending"}
        assert session.query(OfflineImportBatch).count() == 1

    repeated = importer.import_bundle(path)
    assert repeated.no_op is True
    with SessionLocal() as session:
        assert session.query(SourceJDVersion).count() == 2
