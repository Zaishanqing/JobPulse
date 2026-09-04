from typing import Any


class KnowledgeGraphError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 503,
        error_code: str = "knowledge_graph_unavailable",
        details: Any = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.details = details or {}
        self.trace_id = trace_id


class KnowledgeGraphUnavailable(KnowledgeGraphError):
    pass
