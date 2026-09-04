from __future__ import annotations

import gzip
import zipfile

import pytest
from jobgraph_contracts.offline_bundle import BundleMode

from unified_api.offline_export.exporter import BundleExporter
from unified_api.offline_export.manifest import verify_bundle
from unified_api.tests.bundle_test_support import FakeExportRepository, records


def test_full_bootstraps_chain_and_first_incremental_is_rejected(tmp_path):
    repository = FakeExportRepository(records(1, 2, 3))
    exporter = BundleExporter(repository)

    full = exporter.export(output=tmp_path, mode=BundleMode.FULL)

    assert full.record_count == 3
    assert verify_bundle(full.output_path).manifest.parent_bundle_id is None


def test_task_scoped_export_only_contains_task_publications(tmp_path):
    repository = FakeExportRepository(records(1, 2, 3, 4, 5))
    repository.associate("task-a", ["publication-1", "publication-2"])
    repository.associate(
        "task-b", ["publication-2", "publication-3", "publication-4"]
    )
    exporter = BundleExporter(repository)

    summary_a = exporter.export(
        output=tmp_path, mode=BundleMode.FULL, task_id="task-a"
    )
    bundle_a = verify_bundle(summary_a.output_path)
    assert summary_a.record_count == 2
    assert {item.source_record_id for item in bundle_a.records} == {
        "record-1",
        "record-2",
    }

    summary_b = exporter.export(
        output=tmp_path, mode=BundleMode.FULL, task_id="task-b"
    )
    bundle_b = verify_bundle(summary_b.output_path)
    assert summary_b.record_count == 3
    assert {item.source_record_id for item in bundle_b.records} == {
        "record-2",
        "record-3",
        "record-4",
    }

    empty_repository = FakeExportRepository(records(1))
    with pytest.raises(
        ValueError,
        match="No completed bundle exists. Create a full bootstrap bundle first.",
    ):
        BundleExporter(empty_repository).export(
            output=tmp_path / "rejected",
            mode=BundleMode.INCREMENTAL,
        )
    assert empty_repository.batches == {}


def test_full_bundle_rejects_limit_without_creating_batch(tmp_path):
    repository = FakeExportRepository(records(1, 2))
    output = tmp_path / "rejected"

    with pytest.raises(
        ValueError,
        match="Full bootstrap bundles cannot use --limit",
    ):
        BundleExporter(repository).export(
            output=output,
            mode=BundleMode.FULL,
            limit=1,
        )

    assert repository.batches == {}
    assert not output.exists()
    assert list(tmp_path.rglob("*.zip")) == []
    assert list(tmp_path.rglob("*.tmp")) == []


def test_empty_full_bundle_remains_a_valid_bootstrap(tmp_path):
    summary = BundleExporter(FakeExportRepository([])).export(
        output=tmp_path,
        mode=BundleMode.FULL,
    )

    verified = verify_bundle(summary.output_path)
    assert summary.record_count == 0
    assert verified.manifest.mode is BundleMode.FULL
    assert verified.manifest.parent_bundle_id is None
    assert not verified.records


def test_incremental_limit_exports_one_record_at_a_time(tmp_path):
    repository = FakeExportRepository(records(1, 2, 3))
    exporter = BundleExporter(repository)
    full = exporter.export(output=tmp_path, mode=BundleMode.FULL)
    repository.records.extend(records(4, 5))

    incremental = exporter.export(
        output=tmp_path,
        mode=BundleMode.INCREMENTAL,
        limit=1,
    )
    bundle = verify_bundle(incremental.output_path)

    assert bundle.manifest.parent_bundle_id == full.bundle_id
    assert [item.source_record_id for item in bundle.records] == ["record-4"]


def test_empty_incremental_is_rejected_without_advancing_chain(tmp_path):
    repository = FakeExportRepository(records(1, 2, 3))
    exporter = BundleExporter(repository)
    full = exporter.export(output=tmp_path, mode=BundleMode.FULL)
    batch_count = len(repository.batches)
    archives = set(tmp_path.glob("*.zip"))
    latest_before = repository.latest_completed_bundle_id()
    rejected_output = tmp_path / "empty-incremental"

    with pytest.raises(
        ValueError,
        match="No new records are available for incremental export",
    ):
        exporter.export(
            output=rejected_output,
            mode=BundleMode.INCREMENTAL,
        )

    assert len(repository.batches) == batch_count
    assert set(tmp_path.glob("*.zip")) == archives
    assert repository.latest_completed_bundle_id() == latest_before
    assert not rejected_output.exists()
    assert list(tmp_path.rglob("*.tmp")) == []

    repository.records.extend(records(4))
    incremental = exporter.export(
        output=tmp_path,
        mode=BundleMode.INCREMENTAL,
    )
    verified = verify_bundle(incremental.output_path)

    assert verified.manifest.parent_bundle_id == full.bundle_id
    assert [
        item.source_record_id for item in verified.records
    ] == ["record-4"]


def test_full_and_incrementals_form_one_chain_without_reexporting_members(
    tmp_path,
):
    repository = FakeExportRepository(records(1, 2, 3))
    exporter = BundleExporter(repository)

    full = exporter.export(output=tmp_path, mode=BundleMode.FULL)
    repository.records.extend(records(4))
    first_incremental = exporter.export(
        output=tmp_path, mode=BundleMode.INCREMENTAL
    )
    repository.records.extend(records(5))
    second_incremental = exporter.export(
        output=tmp_path, mode=BundleMode.INCREMENTAL
    )

    full_bundle = verify_bundle(full.output_path)
    first_bundle = verify_bundle(first_incremental.output_path)
    second_bundle = verify_bundle(second_incremental.output_path)
    assert [item.source_record_id for item in full_bundle.records] == [
        "record-1",
        "record-2",
        "record-3",
    ]
    assert first_bundle.manifest.parent_bundle_id == full.bundle_id
    assert [
        item.source_record_id for item in first_bundle.records
    ] == ["record-4"]
    assert (
        second_bundle.manifest.parent_bundle_id
        == first_incremental.bundle_id
    )
    assert [
        item.source_record_id for item in second_bundle.records
    ] == ["record-5"]

    restarted_full = exporter.export(output=tmp_path, mode=BundleMode.FULL)
    repository.records.extend(records(6))
    after_restart = exporter.export(
        output=tmp_path, mode=BundleMode.INCREMENTAL
    )
    after_restart_bundle = verify_bundle(after_restart.output_path)
    assert after_restart_bundle.manifest.parent_bundle_id == restarted_full.bundle_id
    assert [
        item.source_record_id for item in after_restart_bundle.records
    ] == ["record-6"]


@pytest.mark.parametrize("status", ["building", "failed", "missing"])
def test_explicit_parent_must_exist_and_be_completed(tmp_path, status):
    repository = FakeExportRepository(records(1))
    exporter = BundleExporter(repository)
    exporter.export(output=tmp_path, mode=BundleMode.FULL)
    if status != "missing":
        repository.batches["parent"] = {
            "bundle_id": "parent-bundle",
            "mode": BundleMode.FULL,
            "parent_bundle_id": None,
            "status": status,
        }

    with pytest.raises(ValueError, match="does not exist or is not completed"):
        exporter.export(
            output=tmp_path,
            mode=BundleMode.INCREMENTAL,
            parent_bundle_id="parent-bundle",
        )


def test_explicit_parent_must_be_latest_completed_bundle(tmp_path):
    repository = FakeExportRepository(records(1, 2, 3))
    exporter = BundleExporter(repository)
    full = exporter.export(output=tmp_path, mode=BundleMode.FULL)
    repository.records.extend(records(4))
    first_incremental = exporter.export(
        output=tmp_path,
        mode=BundleMode.INCREMENTAL,
        parent_bundle_id=full.bundle_id,
    )
    repository.records.extend(records(5))
    batch_count = len(repository.batches)
    archives = set(tmp_path.glob("*.zip"))

    with pytest.raises(
        ValueError,
        match=(
            "Parent bundle is not the latest completed bundle. "
            f"Expected '{first_incremental.bundle_id}', "
            f"received '{full.bundle_id}'."
        ),
    ):
        exporter.export(
            output=tmp_path,
            mode=BundleMode.INCREMENTAL,
            parent_bundle_id=full.bundle_id,
        )

    assert len(repository.batches) == batch_count
    assert set(tmp_path.glob("*.zip")) == archives

    second_incremental = exporter.export(
        output=tmp_path,
        mode=BundleMode.INCREMENTAL,
    )
    second_bundle = verify_bundle(second_incremental.output_path)
    assert (
        second_bundle.manifest.parent_bundle_id
        == first_incremental.bundle_id
    )
    assert [
        item.source_record_id for item in second_bundle.records
    ] == ["record-5"]

    repository.records.extend(records(6))
    latest_explicit = exporter.export(
        output=tmp_path,
        mode=BundleMode.INCREMENTAL,
        parent_bundle_id=second_incremental.bundle_id,
    )
    assert (
        verify_bundle(latest_explicit.output_path).manifest.parent_bundle_id
        == second_incremental.bundle_id
    )


def test_export_writes_complete_utf8_jsonl_gzip_lines(tmp_path):
    summary = BundleExporter(FakeExportRepository(records(1, 2))).export(
        output=tmp_path, mode=BundleMode.FULL
    )

    with zipfile.ZipFile(summary.output_path) as archive:
        data = gzip.decompress(archive.read("jobs.jsonl.gz"))

    assert data.endswith(b"\n")
    assert len(data.splitlines()) == 2
    assert all(
        line.startswith(b"{") and line.endswith(b"}") for line in data.splitlines()
    )
