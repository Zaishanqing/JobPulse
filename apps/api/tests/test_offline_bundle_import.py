from __future__ import annotations

import pytest

from app.contexts.extraction_tasks import ExtractionTaskUseCases
from app.infrastructure.extraction_tasks import SqlAlchemyExtractionTaskUnitOfWork
from app.models.extraction_task import ExtractionTask
from app.models.offline_import import OfflineImportItem
from app.models.source_jd import SourceJDVersion
from app.offline_import import BundleImportConflict, OfflineBundleImporter
from app.offline_import.repository import OfflineImportRepository
from app.offline_import.verifier import verify_bundle
from jobgraph_contracts.offline_bundle import BundleMode
from tests.offline_bundle_test_support import envelope, make_bundle
from tests.runtime_database import reset_database_data, Base, SessionLocal, engine


@pytest.fixture(autouse=True)
def reset_database():
    engine.dispose()
    reset_database_data()
    yield
    engine.dispose()
    reset_database_data()


def _importer():
    use_cases = ExtractionTaskUseCases(
        lambda: SqlAlchemyExtractionTaskUnitOfWork(SessionLocal),
        {},
        3,
    )
    repository = OfflineImportRepository(SessionLocal)
    return (
        OfflineBundleImporter(
            repository, use_cases.import_crawler_envelope_as_jd, "llm"
        ),
        repository,
    )


def test_import_reuses_business_use_case_and_repeat_bundle_is_no_op(tmp_path):
    path = make_bundle(
        tmp_path / "full.zip",
        bundle_id="bundle-full",
        envelopes=[envelope("one", "first"), envelope("two", "second")],
    )
    importer, _ = _importer()

    first = importer.import_bundle(path)
    with SessionLocal() as session:
        counts_before = (
            session.query(SourceJDVersion).count(),
            session.query(ExtractionTask).count(),
            session.query(OfflineImportItem).count(),
        )
    repeated = importer.import_bundle(path)

    assert (first.imported_count, first.skipped_count, first.failed_count) == (
        2,
        0,
        0,
    )
    assert first.status == "completed"
    assert repeated.no_op is True
    with SessionLocal() as session:
        assert (
            session.query(SourceJDVersion).count(),
            session.query(ExtractionTask).count(),
            session.query(OfflineImportItem).count(),
        ) == counts_before


def test_records_with_same_source_version_skip_and_new_version_creates_version(tmp_path):
    first_path = make_bundle(
        tmp_path / "first.zip",
        bundle_id="bundle-first",
        envelopes=[envelope("one", "v1"), envelope("two", "same")],
    )
    second_path = make_bundle(
        tmp_path / "second.zip",
        bundle_id="bundle-second",
        envelopes=[
            envelope("one", "v2", source_version="2"),
            envelope("two", "same"),
        ],
    )
    importer, _ = _importer()

    importer.import_bundle(first_path)
    second = importer.import_bundle(second_path)

    assert (second.imported_count, second.skipped_count) == (1, 1)


def test_same_bundle_id_reimport_is_no_op_and_missing_parent_is_rejected(tmp_path):
    first = make_bundle(
        tmp_path / "identity-a.zip",
        bundle_id="bundle-identity",
        envelopes=[envelope("one", "v1")],
    )
    changed = make_bundle(
        tmp_path / "identity-b.zip",
        bundle_id="bundle-identity",
        envelopes=[envelope("one", "v2")],
    )
    gap = make_bundle(
        tmp_path / "gap.zip",
        bundle_id="bundle-gap",
        envelopes=[envelope("two", "v1")],
        mode=BundleMode.INCREMENTAL,
        parent_bundle_id="bundle-missing",
    )
    undeclared_parent = make_bundle(
        tmp_path / "undeclared-parent.zip",
        bundle_id="bundle-undeclared-parent",
        envelopes=[envelope("three", "v1")],
        mode=BundleMode.INCREMENTAL,
    )
    importer, _ = _importer()
    importer.import_bundle(first)

    with pytest.raises(BundleImportConflict, match="identity conflict"):
        importer.import_bundle(changed)
    with pytest.raises(BundleImportConflict, match="parent bundle"):
        importer.import_bundle(gap)
    with pytest.raises(BundleImportConflict, match="parent bundle"):
        importer.import_bundle(undeclared_parent)
    assert importer.import_bundle(gap, allow_gap=True).status == "completed"
    assert (
        importer.import_bundle(undeclared_parent, allow_gap=True).status
        == "completed"
    )


def test_failed_or_importing_bundle_requires_explicit_retry(tmp_path):
    path = make_bundle(
        tmp_path / "retry.zip",
        bundle_id="bundle-retry",
        envelopes=[envelope("one", "v1")],
    )
    importer, repository = _importer()
    batch_id = repository.create_batch(verify_bundle(path))
    repository.fail_batch(batch_id, "interrupted")

    with pytest.raises(BundleImportConflict, match="explicit --retry"):
        importer.import_bundle(path)
    assert importer.import_bundle(path, retry=True).status == "completed"


def test_damaged_bundle_writes_no_business_or_import_rows(tmp_path):
    path = make_bundle(
        tmp_path / "damaged.zip",
        bundle_id="bundle-damaged",
        raw_lines=[b"{broken-json"],
    )
    importer, _ = _importer()

    with pytest.raises(Exception, match="line 1"):
        importer.import_bundle(path)

    with SessionLocal() as session:
        assert session.query(OfflineImportItem).count() == 0
