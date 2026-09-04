"""SQLAlchemy release import ledger adapter."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    PublishedFactImport,
    PublishedFactReleaseLink,
    ReleaseImportBatch,
    ReleaseImportItem,
)
from app.domain.structured_facts import PublishedJDFact
from jobgraph_contracts.release_manifest import ReleaseManifestV1


class SqlAlchemyReleaseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_manifest_hash(self, release_id: str) -> str | None:
        row = self.session.scalar(
            select(ReleaseImportBatch).where(ReleaseImportBatch.release_id == release_id)
        )
        return row.manifest_hash if row is not None else None

    def parent_exists(self, release_id: str) -> bool:
        return self.find_manifest_hash(release_id) is not None

    def save_release(
        self,
        manifest: ReleaseManifestV1,
        manifest_hash: str,
        facts: tuple[PublishedJDFact, ...],
        document_ids: tuple[str, ...],
    ) -> None:
        self.session.add(
            ReleaseImportBatch(
                release_id=manifest.release_id,
                manifest_hash=manifest_hash,
                manifest=manifest.model_dump(mode="json"),
                record_count=len(facts),
            )
        )
        self.session.flush()
        for ordinal, (fact, document_id) in enumerate(zip(facts, document_ids, strict=True)):
            imported = self.session.scalar(
                select(PublishedFactImport).where(
                    PublishedFactImport.source_system == fact.source_system,
                    PublishedFactImport.source_fact_id == fact.source_fact_id,
                    PublishedFactImport.source_fact_version == fact.source_fact_version,
                )
            )
            if imported is None:
                raise RuntimeError("release fact import was not persisted")
            self.session.add(
                ReleaseImportItem(
                    release_id=manifest.release_id,
                    ordinal=ordinal,
                    source_system=fact.source_system,
                    source_fact_id=fact.source_fact_id,
                    source_fact_version=fact.source_fact_version,
                    source_version=fact.source_version,
                    document_id=document_id,
                )
            )
            self.session.add(
                PublishedFactReleaseLink(
                    published_fact_import_id=imported.id,
                    release_id=manifest.release_id,
                )
            )
