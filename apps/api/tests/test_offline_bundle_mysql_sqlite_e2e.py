from __future__ import annotations

import os
import shutil

import pytest

if os.getenv("JOBPULSE_MYSQL_E2E") != "1":
    pytest.skip("Set JOBPULSE_MYSQL_E2E=1 for the local MySQL acceptance test", allow_module_level=True)

from app.contexts.extraction_tasks import ExtractionTaskUseCases
from app.infrastructure.extraction_tasks import SqlAlchemyExtractionTaskUnitOfWork
from app.models.source_jd import SourceJDVersion
from app.offline_import import BundleVerificationError, OfflineBundleImporter
from app.offline_import.repository import OfflineImportRepository
from jobgraph_contracts.offline_bundle import BundleMode
from tests.offline_bundle_test_support import envelope, replace_member
from tests.runtime_database import Base, SessionLocal, engine, reset_database_data
from unified_api.database import ensure_schema, get_conn
from unified_api.offline_export.exporter import BundleExporter
from unified_api.offline_export.repository import MySQLExportRepository
from unified_api.offline_export.staging import ensure_export_candidate_in_transaction


def _use_cases() -> ExtractionTaskUseCases:
    return ExtractionTaskUseCases(
        lambda: SqlAlchemyExtractionTaskUnitOfWork(SessionLocal),
        {},
        3,
    )


def _reset_sqlite() -> None:
    engine.dispose()
    reset_database_data()


def test_mysql_bundle_to_empty_and_partial_sqlite(tmp_path) -> None:
    ensure_schema()
    connection = get_conn()
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM crawler_export_members")
            cursor.execute("DELETE FROM crawler_export_batches")
            cursor.execute("DELETE FROM crawler_publications")
        connection.commit()

        # Acceptance mix: five new identities, three records later preloaded
        # with the same hash, and two records later preloaded with an old hash.
        target = [
            *(envelope(f"new-{number}", f"new text {number}") for number in range(5)),
            *(envelope(f"same-{number}", f"same text {number}") for number in range(3)),
            *(envelope(f"changed-{number}", f"new version {number}") for number in range(2)),
        ]
        for number, value in enumerate(target):
            ensure_export_candidate_in_transaction(
                connection,
                value,
                source_kind="mysql_e2e",
                source_job_id=f"crawl-{number}",
            )
        connection.commit()
    finally:
        connection.close()

    exported = BundleExporter(MySQLExportRepository()).export(
        output=tmp_path,
        mode=BundleMode.FULL,
        producer_git_commit="mysql-sqlite-e2e",
    )
    assert exported.record_count == 10

    _reset_sqlite()
    use_cases = _use_cases()
    importer = OfflineBundleImporter(
        OfflineImportRepository(SessionLocal),
        use_cases.import_crawler_envelope_as_jd,
        "llm",
    )
    empty_result = importer.import_bundle(exported.output_path)
    assert (empty_result.imported_count, empty_result.skipped_count) == (10, 0)

    _reset_sqlite()
    use_cases = _use_cases()
    for number in range(3):
        use_cases.import_crawler_envelope_as_jd(
            envelope(f"same-{number}", f"same text {number}"), "llm"
        )
    for number in range(2):
        use_cases.import_crawler_envelope_as_jd(
            envelope(f"changed-{number}", f"old version {number}"), "llm"
        )
    importer = OfflineBundleImporter(
        OfflineImportRepository(SessionLocal),
        use_cases.import_crawler_envelope_as_jd,
        "llm",
    )
    partial_result = importer.import_bundle(exported.output_path)
    repeated_result = importer.import_bundle(exported.output_path)
    assert (partial_result.imported_count, partial_result.skipped_count) == (7, 3)
    assert repeated_result.no_op is True

    with SessionLocal() as session:
        versions_before = session.query(SourceJDVersion).count()
    tampered = tmp_path / "tampered.zip"
    shutil.copyfile(exported.output_path, tampered)
    replace_member(tampered, "jobs.jsonl.gz", b"damaged")
    with pytest.raises(BundleVerificationError):
        importer.import_bundle(tampered)
    with SessionLocal() as session:
        assert session.query(SourceJDVersion).count() == versions_before
