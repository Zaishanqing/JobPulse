"""Compatibility import for scripts; production routing lives in app.api.v1."""

from app.api.v1.knowledge_graph import router


__all__ = ["router"]
