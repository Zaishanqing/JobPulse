from app.contexts.platform._ports.embeddings import (
    EmbeddingGatewayPort,
    EmbeddingResult,
    EmbeddingSourcePort,
    SimilarityResult,
    VectorGatewayPort,
    VectorHit,
    VectorMetadata,
    VectorSearchResult,
)
from app.contexts.platform._ports.files import (
    AccountActor,
    BlobStoragePort,
    FileRecord,
    FileRepository,
    FileUnitOfWork,
    FileUploadWorkflowPort,
)
from app.contexts.platform._ports.ocr import (
    OCRExtractionOutcome,
    OCRExtractionPort,
    OCRRepository,
    OCRResultRecord,
    OCRUnitOfWork,
    TaskRecord,
)
from app.contexts.platform._ports.outbox import (
    OutboxOperationsRepository,
    OutboxOperationsUnitOfWork,
)
from app.contexts.platform._ports.system import (
    FrozenJsonObject,
    SystemConfigRecord,
    SystemConfigRepository,
    SystemConfigUnitOfWork,
    SystemStatusPort,
)

__all__ = [
    "AccountActor",
    "BlobStoragePort",
    "EmbeddingGatewayPort",
    "EmbeddingResult",
    "EmbeddingSourcePort",
    "FileRecord",
    "FileRepository",
    "FileUnitOfWork",
    "FileUploadWorkflowPort",
    "FrozenJsonObject",
    "OCRExtractionOutcome",
    "OCRExtractionPort",
    "OCRRepository",
    "OCRResultRecord",
    "OCRUnitOfWork",
    "OutboxOperationsRepository",
    "OutboxOperationsUnitOfWork",
    "SimilarityResult",
    "SystemConfigRecord",
    "SystemConfigRepository",
    "SystemConfigUnitOfWork",
    "SystemStatusPort",
    "TaskRecord",
    "VectorGatewayPort",
    "VectorHit",
    "VectorMetadata",
    "VectorSearchResult",
]
