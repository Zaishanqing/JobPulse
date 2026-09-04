from __future__ import annotations

from dataclasses import dataclass

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1

from app.contexts.source_jds.ports import (
    SourceJDRecord,
    SourceJDUoWFactory,
    SourceJDVersionRecord,
)


class SourceJDNotFound(LookupError):
    pass


class InvalidSourceJDEnvelope(ValueError):
    pass


class SourceJDImportConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ImportSourceJDResult:
    source_jd_id: str
    source_jd_version_id: str
    created_source: bool
    created_version: bool
    is_latest: bool
    source_version: str


class SourceJDUseCases:
    def __init__(self, uow_factory: SourceJDUoWFactory) -> None:
        self._uow_factory = uow_factory

    def import_source_jd(self, envelope: CrawlerJDEnvelopeV1) -> ImportSourceJDResult:
        if not isinstance(envelope, CrawlerJDEnvelopeV1):
            raise TypeError("envelope must be CrawlerJDEnvelopeV1")

        with self._uow_factory() as uow:
            uow.acquire_import_lock(envelope.source_platform, envelope.source_record_id)
            result = import_source_jd_in_uow(envelope, uow)
            uow.commit()
            return result

    def get_source_jd(self, source_jd_id: str) -> SourceJDRecord:
        with self._uow_factory() as uow:
            source = uow.source_jds.get(source_jd_id)
            if source is None:
                raise SourceJDNotFound("SourceJD not found")
            return source

    def list_versions(self, source_jd_id: str) -> tuple[SourceJDVersionRecord, ...]:
        with self._uow_factory() as uow:
            if uow.source_jds.get(source_jd_id) is None:
                raise SourceJDNotFound("SourceJD not found")
            return uow.source_jds.list_versions(source_jd_id)

    def get_version(self, version_id: str) -> SourceJDVersionRecord:
        with self._uow_factory() as uow:
            version = uow.source_jds.get_version(version_id)
            if version is None:
                raise SourceJDNotFound("SourceJDVersion not found")
            return version


def import_source_jd_in_uow(envelope: CrawlerJDEnvelopeV1, uow) -> ImportSourceJDResult:
    """Import using an already-open UoW; the caller owns locking and commit."""
    source = uow.source_jds.get_by_identity(
        envelope.source_platform,
        envelope.source_record_id,
        for_update=True,
    )
    created_source = source is None
    if source is None:
        source = uow.source_jds.add_source(
            envelope.source_platform, envelope.source_record_id
        )
    version = uow.source_jds.get_version_by_source_version(
        source.id, envelope.source_version
    )
    created_version = version is None
    if version is not None and version.content_hash != envelope.content_hash:
        raise SourceJDImportConflict(
            "SourceJDVersion already exists for source_version but raw content differs"
        )
    if version is None:
        version = uow.source_jds.add_version(source.id, envelope)
        source = uow.source_jds.set_latest(source.id, version.id)
    return ImportSourceJDResult(
        source_jd_id=source.id,
        source_jd_version_id=version.id,
        created_source=created_source,
        created_version=created_version,
        is_latest=source.latest_version_id == version.id,
        source_version=version.source_version,
    )
