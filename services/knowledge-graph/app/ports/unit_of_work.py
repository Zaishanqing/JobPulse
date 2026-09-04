from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from app.ports.repositories import (
    AlgorithmConfigRepository,
    AuditRepository,
    CatalogSnapshotRepository,
    DocumentRepository,
    ExtractionRepository,
    NormalizationRepository,
    QualityRepository,
    GraphVersionRepository,
    GraphBuildRepository,
    BuildJobRepository,
    GraphDraftRepository,
    PublishedFactRepository,
    InnovationRepository,
    ReviewObjectEffectPort,
    ReviewTaskRepository,
    ReleaseRepository,
)


class UnitOfWork(Protocol):
    catalog_snapshots: CatalogSnapshotRepository
    documents: DocumentRepository
    extractions: ExtractionRepository
    normalizations: NormalizationRepository
    graph_builds: GraphBuildRepository
    build_jobs: BuildJobRepository
    graph_drafts: GraphDraftRepository
    graph_versions: GraphVersionRepository
    review_tasks: ReviewTaskRepository
    review_effects: ReviewObjectEffectPort
    audits: AuditRepository
    quality: QualityRepository
    algorithm_configs: AlgorithmConfigRepository
    published_facts: PublishedFactRepository
    innovation: InnovationRepository
    releases: ReleaseRepository

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
