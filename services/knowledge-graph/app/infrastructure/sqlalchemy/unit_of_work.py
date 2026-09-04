from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.sqlalchemy.repository_adapters import (
    SqlAlchemyAuditRepository,
    SqlAlchemyCatalogSnapshotRepository,
    SqlAlchemyAlgorithmConfigRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyExtractionRepository,
    SqlAlchemyGraphDraftRepository,
    SqlAlchemyGraphVersionRepository,
    SqlAlchemyNormalizationRepository,
    SqlAlchemyPublishedFactRepository,
    SqlAlchemyQualityRepository,
    SqlAlchemyReviewTaskRepository,
)
from app.infrastructure.sqlalchemy.graph_build_repository import SqlAlchemyGraphBuildRepository
from app.infrastructure.sqlalchemy.build_jobs import SqlAlchemyBuildJobRepository
from app.infrastructure.sqlalchemy.review_handlers import (
    SqlAlchemyReviewObjectEffectAdapter,
)
from app.infrastructure.sqlalchemy.innovation_repository import (
    SqlAlchemyInnovationRepository,
)
from app.infrastructure.sqlalchemy.release_repository import SqlAlchemyReleaseRepository


class SqlAlchemyUnitOfWork:
    """One session and transaction per application use-case invocation."""

    def __init__(
        self, session_factory: sessionmaker[Session], *, close_session: bool = True
    ):
        self.session_factory = session_factory
        self.close_session = close_session
        self.session: Session | None = None

    def __enter__(self):
        self.session = self.session_factory()
        self.documents = SqlAlchemyDocumentRepository(self.session)
        self.catalog_snapshots = SqlAlchemyCatalogSnapshotRepository(self.session)
        self.extractions = SqlAlchemyExtractionRepository(self.session)
        self.normalizations = SqlAlchemyNormalizationRepository(self.session)
        self.graph_builds = SqlAlchemyGraphBuildRepository(self.session)
        self.build_jobs = SqlAlchemyBuildJobRepository(self.session)
        self.graph_drafts = SqlAlchemyGraphDraftRepository(self.session)
        self.graph_versions = SqlAlchemyGraphVersionRepository(self.session)
        self.review_tasks = SqlAlchemyReviewTaskRepository(self.session)
        self.review_effects = SqlAlchemyReviewObjectEffectAdapter(self.session)
        self.audits = SqlAlchemyAuditRepository(self.session)
        self.quality = SqlAlchemyQualityRepository(self.session)
        self.algorithm_configs = SqlAlchemyAlgorithmConfigRepository(self.session)
        self.published_facts = SqlAlchemyPublishedFactRepository(self.session)
        self.releases = SqlAlchemyReleaseRepository(self.session)
        self.innovation = SqlAlchemyInnovationRepository(self.session)
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self.session is not None
        if exc_type is not None:
            self.rollback()
        if self.close_session:
            self.session.close()

    def commit(self) -> None:
        assert self.session is not None
        self.session.commit()

    def rollback(self) -> None:
        assert self.session is not None
        self.session.rollback()
