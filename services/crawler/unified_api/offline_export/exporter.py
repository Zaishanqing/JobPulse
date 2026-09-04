from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from jobgraph_contracts.offline_bundle import (
    BUNDLE_SHA256SUMS_FILE,
    BundleManifestV1,
    BundleMode,
    BundleProducer,
    CrawlTimeRange,
)

from unified_api.offline_export.contracts import ExportSummary
from unified_api.offline_export.repository import ExportRepository


def validate_export_request(*, mode: BundleMode, limit: int | None) -> None:
    if mode is BundleMode.FULL and limit is not None:
        raise ValueError("Full bootstrap bundles cannot use --limit.")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")


def _sync_file(path: Path) -> None:
    # Windows requires a writable CRT descriptor for fsync in the bundled
    # runtime; r+b preserves content while providing a portable flush handle.
    with path.open("r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())


class BundleExporter:
    def __init__(self, repository: ExportRepository) -> None:
        self._repository = repository

    def export(
        self,
        *,
        output: Path,
        mode: BundleMode,
        parent_bundle_id: str | None = None,
        limit: int | None = None,
        producer_git_commit: str = "unknown",
        task_id: str | None = None,
    ) -> ExportSummary:
        validate_export_request(mode=mode, limit=limit)
        if mode is BundleMode.FULL:
            parent_bundle_id = None
        else:
            latest_parent = self._repository.latest_completed_bundle_id()
            if latest_parent is None:
                raise ValueError(
                    "No completed bundle exists. "
                    "Create a full bootstrap bundle first."
                )
            if parent_bundle_id is None:
                parent_bundle_id = latest_parent
            elif not self._repository.is_completed_bundle(parent_bundle_id):
                raise ValueError(
                    f"Parent bundle {parent_bundle_id!r} does not exist "
                    "or is not completed."
                )
            elif parent_bundle_id != latest_parent:
                raise ValueError(
                    "Parent bundle is not the latest completed bundle. "
                    f"Expected {latest_parent!r}, received {parent_bundle_id!r}."
                )

        records = self._repository.list_records(
            mode=mode, limit=limit, task_id=task_id
        )
        records.sort(
            key=lambda item: (
                item.envelope.source_platform,
                item.envelope.source_record_id,
                item.envelope.source_version,
                item.publication_id,
            )
        )
        # An empty incremental archive would become a completed parent that
        # operators may reasonably skip transferring. Reject it before any
        # export-side write so the next real increment still points to the
        # latest bundle that member computers can possess.
        if mode is BundleMode.INCREMENTAL and not records:
            raise ValueError(
                "No new records are available for incremental export."
            )

        output = output.resolve()
        output.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        sequence = int(uuid4().hex[:8], 16) % 10000
        bundle_id = f"bundle-{timestamp}-{sequence:04d}"
        file_name = f"nfbs-jd-bundle-v1-{timestamp}-{sequence:04d}.zip"
        final_path = output / file_name
        if final_path.exists():
            raise FileExistsError(f"Refusing to overwrite {final_path}")
        batch_id = self._repository.create_batch(
            bundle_id=bundle_id,
            mode=mode,
            parent_bundle_id=parent_bundle_id,
        )
        workspace = Path(tempfile.mkdtemp(prefix=f".{bundle_id}-", dir=output))
        zip_temp = output / f".{file_name}.tmp"
        try:
            data_path = workspace / "jobs.jsonl.gz"
            with data_path.open("xb") as raw_stream:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=raw_stream, mtime=0
                ) as compressed_stream:
                    for record in records:
                        line = (
                            json.dumps(
                                record.envelope.model_dump(mode="json"),
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                            + b"\n"
                        )
                        compressed_stream.write(line)
                raw_stream.flush()
                os.fsync(raw_stream.fileno())
            compressed_bytes = data_path.read_bytes()
            uncompressed_bytes = gzip.decompress(compressed_bytes)
            compressed_sha256 = hashlib.sha256(compressed_bytes).hexdigest()
            uncompressed_sha256 = hashlib.sha256(uncompressed_bytes).hexdigest()
            crawl_times = [item.envelope.crawl_time for item in records]
            manifest = BundleManifestV1(
                bundle_id=bundle_id,
                created_at=now,
                producer=BundleProducer(
                    application="nfbs-unified-crawler",
                    git_commit=producer_git_commit,
                ),
                mode=mode,
                parent_bundle_id=parent_bundle_id,
                record_count=len(records),
                crawl_time_range=CrawlTimeRange(
                    minimum=min(crawl_times) if crawl_times else None,
                    maximum=max(crawl_times) if crawl_times else None,
                ),
                compressed_sha256=compressed_sha256,
                uncompressed_sha256=uncompressed_sha256,
            )
            manifest_path = workspace / "manifest.json"
            manifest_bytes = (
                json.dumps(
                    manifest.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            manifest_path.write_bytes(manifest_bytes)
            _sync_file(manifest_path)
            sha256sums_path = workspace / BUNDLE_SHA256SUMS_FILE
            sha256sums_path.write_text(
                f"{compressed_sha256}  {data_path.name}\n"
                f"{hashlib.sha256(manifest_bytes).hexdigest()}  manifest.json\n",
                encoding="utf-8",
                newline="\n",
            )
            _sync_file(sha256sums_path)
            with zipfile.ZipFile(
                zip_temp, mode="x", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for name in ("manifest.json", "jobs.jsonl.gz", BUNDLE_SHA256SUMS_FILE):
                    archive.write(workspace / name, arcname=name)
            _sync_file(zip_temp)
            os.replace(zip_temp, final_path)
            self._repository.complete_batch(
                batch_id=batch_id,
                records=records,
                file_name=file_name,
            )
            return ExportSummary(
                batch_id=batch_id,
                bundle_id=bundle_id,
                output_path=final_path,
                record_count=len(records),
            )
        except Exception as exc:
            zip_temp.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            self._repository.fail_batch(batch_id, str(exc))
            raise
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
