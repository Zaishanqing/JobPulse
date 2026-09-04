from __future__ import annotations

from datetime import datetime, timezone

import pytest
from jobgraph_contracts.offline_bundle import BundleMode

from unified_api.offline_export.exporter import BundleExporter
from unified_api.tests.bundle_test_support import FakeExportRepository, records


def test_existing_final_file_is_never_overwritten(tmp_path, monkeypatch):
    repository = FakeExportRepository(records(1))
    exporter = BundleExporter(repository)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 26, 8, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(
        "unified_api.offline_export.exporter.datetime", FixedDatetime
    )
    monkeypatch.setattr(
        "unified_api.offline_export.exporter.uuid4",
        lambda: type("Fixed", (), {"hex": "00000000"})(),
    )
    first = exporter.export(output=tmp_path, mode=BundleMode.FULL)
    original = first.output_path.read_bytes()

    with pytest.raises(FileExistsError):
        exporter.export(output=tmp_path, mode=BundleMode.FULL)

    assert first.output_path.read_bytes() == original


def test_failure_removes_temporary_and_final_archives(tmp_path):
    repository = FakeExportRepository(records(1))
    repository.fail_completion = True

    with pytest.raises(RuntimeError, match="completion failed"):
        BundleExporter(repository).export(
            output=tmp_path, mode=BundleMode.FULL
        )

    assert list(tmp_path.glob("nfbs-jd-bundle-*.zip")) == []
    assert not any(path.suffix == ".tmp" for path in tmp_path.iterdir())
    assert next(iter(repository.batches.values()))["status"] == "failed"
