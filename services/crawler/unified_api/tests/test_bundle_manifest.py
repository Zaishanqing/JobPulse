from __future__ import annotations

from jobgraph_contracts.offline_bundle import BundleManifestV1, BundleMode

from unified_api.offline_export.exporter import BundleExporter
from unified_api.offline_export.manifest import verify_bundle
from unified_api.tests.bundle_test_support import FakeExportRepository, records


def test_exported_two_file_bundle_and_stable_order_are_verifiable(tmp_path):
    repository = FakeExportRepository(records(3, 1, 2))
    summary = BundleExporter(repository).export(
        output=tmp_path,
        mode=BundleMode.FULL,
        producer_git_commit="abc123",
    )

    verified = verify_bundle(summary.output_path)

    assert isinstance(verified.manifest, BundleManifestV1)
    assert verified.manifest.record_count == 3
    assert verified.manifest.mode is BundleMode.FULL
    assert verified.manifest.parent_bundle_id is None
    assert [item.source_record_id for item in verified.records] == [
        "record-1",
        "record-2",
        "record-3",
    ]
