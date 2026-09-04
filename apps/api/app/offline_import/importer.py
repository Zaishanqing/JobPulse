from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Literal

from app.contexts.extraction_tasks import ImportAndScheduleResult
from app.offline_import.contracts import BundleImportConflict, ImportSummary
from app.offline_import.repository import OfflineImportRepository
from app.offline_import.verifier import verify_bundle
from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1
from jobgraph_contracts.offline_bundle import BundleMode


class OfflineBundleImporter:
    def __init__(
        self,
        repository: OfflineImportRepository,
        import_envelope: Callable[
            [CrawlerJDEnvelopeV1, Literal["llm", "rule"]], ImportAndScheduleResult
        ],
        extraction_mode: Literal["llm", "rule"],
    ) -> None:
        self._repository = repository
        self._import_envelope = import_envelope
        self._extraction_mode = extraction_mode

    def import_bundle(
        self,
        path: Path,
        *,
        allow_gap: bool = False,
        retry: bool = False,
    ) -> ImportSummary:
        # Archive validation completes before any batch or business write.
        # A damaged transport package therefore cannot partially enter SQLite.
        bundle = verify_bundle(path)
        manifest = bundle.manifest
        existing = self._repository.find_batch(manifest.bundle_id)
        if existing is not None:
            if existing.bundle_digest != bundle.bundle_digest:
                raise BundleImportConflict(
                    "Bundle identity conflict: bundle_id already exists with a different digest"
                )
            if existing.status == "completed":
                summary = self._repository.summary(manifest.bundle_id)
                assert summary is not None
                return replace(
                    summary,
                    no_op=True,
                )
            if not retry:
                raise BundleImportConflict(
                    "Existing non-completed bundle requires explicit --retry"
                )
            batch_id = existing.batch_id
            self._repository.prepare_retry(batch_id)
        else:
            if (
                manifest.mode is BundleMode.INCREMENTAL
                and not allow_gap
                and (
                    manifest.parent_bundle_id is None
                    or not self._repository.parent_is_completed(manifest.parent_bundle_id)
                )
            ):
                raise BundleImportConflict(
                    "Incremental parent bundle is missing or not completed locally; "
                    "use --allow-gap only after manual confirmation"
                )
            batch_id = self._repository.create_batch(bundle)

        try:
            for record in bundle.records:
                current = self._repository.item_status(batch_id, record.line_number)
                if current in {"imported", "skipped"}:
                    continue
                self._repository.ensure_pending_item(batch_id, record)
                try:
                    result = self._import_envelope(
                        record.envelope, self._extraction_mode
                    )
                except Exception as exc:
                    self._repository.finish_item(
                        batch_id=batch_id,
                        line_number=record.line_number,
                        status="failed",
                        error_code=exc.__class__.__name__,
                        error_message=str(exc),
                    )
                    continue
                self._repository.finish_item(
                    batch_id=batch_id,
                    line_number=record.line_number,
                    status="imported" if result.created_version else "skipped",
                    source_jd_id=result.source_jd_id,
                    source_jd_version_id=result.source_jd_version_id,
                    extraction_task_id=result.extraction_task_id,
                )
            return self._repository.finalize(batch_id)
        except Exception as exc:
            self._repository.fail_batch(batch_id, str(exc))
            raise
