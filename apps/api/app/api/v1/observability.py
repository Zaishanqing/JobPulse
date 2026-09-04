from collections.abc import Mapping

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.core.response import error_response, success_response
from app.api.dependencies.system import get_system_queries
from app.contexts.platform import QuerySystemStatus


router = APIRouter(tags=["observability"])


def check_readiness(queries: QuerySystemStatus) -> tuple[bool, dict[str, object]]:
    """Compatibility probe delegating to the readiness use case."""

    return queries.readiness()


@router.get("/readiness")
def readiness_check(queries: QuerySystemStatus = Depends(get_system_queries)):
    ready, data = check_readiness(queries)
    if ready:
        return success_response(data=data)
    return JSONResponse(
        status_code=503,
        content=error_response(message="Service is not ready", code=503, data=data),
    )


@router.get("/extraction-modes/readiness")
def extraction_modes_readiness(
    queries: QuerySystemStatus = Depends(get_system_queries),
):
    _, data = check_readiness(queries)
    checks = data.get("checks", {}) if isinstance(data, Mapping) else {}
    jd = checks.get("jd_extraction", {}) if isinstance(checks, Mapping) else {}
    return success_response(data={"jd": jd})
