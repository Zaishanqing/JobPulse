from fastapi import Request

from app.contexts.knowledge_graph import ManageKnowledgeGraphIntegration
from app.api.dependencies.container import get_application_container


def get_knowledge_graph_handlers(request: Request) -> ManageKnowledgeGraphIntegration:
    return get_application_container(request).knowledge_graph
