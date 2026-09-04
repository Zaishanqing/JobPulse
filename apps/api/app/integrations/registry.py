from dataclasses import dataclass
from functools import lru_cache

from app.core.config import settings
from app.integrations.contracts import (
    DocumentParser,
    EmbeddingProvider,
    EvidenceRetriever,
    FileStorage,
    LLMProvider,
    OCRProvider,
    TaskQueue,
    TrendSourceCrawler,
    VectorStore,
)
from app.integrations.local import (
    DatabaseSyncTaskQueue,
    DeterministicEmbeddingProvider,
    DisabledLLMProvider,
    DisabledOCRProvider,
    DisabledTrendSourceCrawler,
    InMemoryVectorStore,
    KeywordEvidenceRetriever,
    LocalFileStorage,
    PlainTextDocumentParser,
    TesseractOCRProvider,
)


@dataclass(frozen=True)
class IntegrationRegistry:
    llm: LLMProvider
    ocr: OCRProvider
    document_parser: DocumentParser
    embedding: EmbeddingProvider
    vector_store: VectorStore
    task_queue: TaskQueue
    file_storage: FileStorage
    evidence_retriever: EvidenceRetriever
    trend_crawler: TrendSourceCrawler

    def statuses(self) -> dict[str, dict]:
        adapters = {
            "llm": self.llm,
            "ocr": self.ocr,
            "document_parser": self.document_parser,
            "embedding": self.embedding,
            "vector_store": self.vector_store,
            "task_queue": self.task_queue,
            "file_storage": self.file_storage,
            "evidence_retriever": self.evidence_retriever,
            "trend_crawler": self.trend_crawler,
        }
        return {name: adapter.status().as_dict() for name, adapter in adapters.items()}


@lru_cache(maxsize=1)
def get_integration_registry() -> IntegrationRegistry:
    # Only safe local/disabled providers are selectable in this batch. A real
    # provider must be implemented and explicitly wired before its config name
    # is accepted.
    return IntegrationRegistry(
        llm=DisabledLLMProvider(),
        ocr=TesseractOCRProvider() if settings.OCR_PROVIDER == "tesseract" else DisabledOCRProvider(),
        document_parser=PlainTextDocumentParser(),
        embedding=DeterministicEmbeddingProvider(settings.EMBEDDING_DIMENSION),
        vector_store=InMemoryVectorStore(),
        task_queue=DatabaseSyncTaskQueue(),
        file_storage=LocalFileStorage(settings.UPLOAD_DIR),
        evidence_retriever=KeywordEvidenceRetriever(),
        trend_crawler=DisabledTrendSourceCrawler(),
    )


def reset_integration_registry() -> None:
    # Test/runtime reset must also clear adapters already captured by the
    # composition root; clearing only the factory cache leaves those instances
    # alive and leaks in-memory vectors/graphs across isolated runs.
    if get_integration_registry.cache_info().currsize:
        registry = get_integration_registry()
        if isinstance(registry.vector_store, InMemoryVectorStore):
            registry.vector_store.clear()
    get_integration_registry.cache_clear()
