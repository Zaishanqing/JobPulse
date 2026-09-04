from app.infrastructure.sqlalchemy.query_auth import AuthenticationQueryMixin
from app.infrastructure.sqlalchemy.query_builds import BuildQueryMixin
from app.infrastructure.sqlalchemy.query_catalog import CatalogQueryMixin
from app.infrastructure.sqlalchemy.query_documents import DocumentQueryMixin
from app.infrastructure.sqlalchemy.query_evidence import EvidenceQueryMixin
from app.infrastructure.sqlalchemy.query_graphs import GraphQueryMixin
from app.infrastructure.sqlalchemy.query_innovation import InnovationQueryMixin
from app.infrastructure.sqlalchemy.query_profiles import PositionProfileQueryMixin
from app.infrastructure.sqlalchemy.query_reviews import ReviewQueryMixin
from app.infrastructure.sqlalchemy.query_versions import VersionQueryMixin


class SqlAlchemyKnowledgeGraphQueryService(
    AuthenticationQueryMixin,
    DocumentQueryMixin,
    CatalogQueryMixin,
    BuildQueryMixin,
    GraphQueryMixin,
    PositionProfileQueryMixin,
    EvidenceQueryMixin,
    ReviewQueryMixin,
    VersionQueryMixin,
    InnovationQueryMixin,
):
    """Composed query adapter; each mixin owns one read concern."""
