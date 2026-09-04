from fastapi import Request

from app.application.queries import KnowledgeGraphQueryService


def get_query_service(request: Request) -> KnowledgeGraphQueryService:
    return request.state.query_service
