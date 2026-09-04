from app.infrastructure.fake_vector_adapters import (
    FakeEmbeddingAdapter,
    FakeVectorStoreAdapter,
)
from app.infrastructure.http_sources import (
    HttpCVProfileSource,
    HttpPositionProfileSource,
)
from app.infrastructure.memory_repositories import InMemoryPersistence
from app.infrastructure.memory_sources import (
    InMemoryCVProfileSource,
    InMemoryPositionProfileSource,
)
from app.infrastructure.memory_task_queue import InMemoryTaskQueue
from app.infrastructure.persistence_configuration import build_persistence
from app.infrastructure.queue_configuration import build_task_queue
from app.infrastructure.redis_task_queue import RedisTaskQueue
from app.infrastructure.relation_sources import (
    HttpSkillRelationSource,
    InMemorySkillRelationSource,
)
from app.infrastructure.resource_authorization import (
    HttpApplicationGrantAdapter,
    HttpCVAuthorizationAdapter,
    HttpEnterpriseJobGrantAdapter,
    InMemoryApplicationGrantAdapter,
    InMemoryCVAuthorizationAdapter,
)
from app.infrastructure.sqlalchemy_repositories import SQLAlchemyPersistence

__all__ = [
    "HttpCVProfileSource",
    "HttpCVAuthorizationAdapter",
    "HttpApplicationGrantAdapter",
    "HttpEnterpriseJobGrantAdapter",
    "InMemoryCVAuthorizationAdapter",
    "InMemoryApplicationGrantAdapter",
    "HttpPositionProfileSource",
    "InMemoryCVProfileSource",
    "InMemoryPersistence",
    "InMemoryTaskQueue",
    "InMemoryPositionProfileSource",
    "HttpSkillRelationSource",
    "InMemorySkillRelationSource",
    "FakeEmbeddingAdapter",
    "FakeVectorStoreAdapter",
    "SQLAlchemyPersistence",
    "RedisTaskQueue",
    "build_persistence",
    "build_task_queue",
]
