from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from jobgraph_contracts.offline_api_docs import install_offline_api_docs

import app.models
from app.api.v1.router import router as api_v1_router
from app.application_container import ApplicationContainer
from app.bootstrap.container import _build_runtime
from app.contexts.knowledge_graph import (
    KnowledgeGraphIntegrationConflict,
    KnowledgeGraphIntegrationDisabled,
    KnowledgeGraphIntegrationNotFound,
    KnowledgeGraphIntegrationRuleViolation,
)
from app.core.config import Settings, settings
from app.core.logging import configure_logging
from app.core.request_context import create_trace_id, reset_trace_id, set_trace_id
from app.core.response import error_response, success_response
from app.integrations.emerging_discovery.exceptions import EmergingDiscoveryError
from app.integrations.knowledge_graph.exceptions import KnowledgeGraphError
from app.api.contracts.jd.errors import UnsupportedSchemaVersion
from app.domain.errors import (
    ExternalGatewayError,
    NoReleasedJDFacts,
    PermissionDenied,
    ProjectionConflict,
)


def _format_validation_location(location: tuple | list) -> str:
    """Render a Pydantic/FastAPI error location as a stable readable path.

    Examples:
        ("body", "items", 0, "name") -> "body.items[0].name"
        ("query", "page") -> "query.page"
    """
    parts: list[str] = []
    for index, part in enumerate(location or ()):
        if isinstance(part, int):
            if not parts:
                parts.append(f"body[{part}]")
            else:
                parts[-1] = f"{parts[-1]}[{part}]"
        else:
            parts.append(str(part))
    return ".".join(parts) if parts else "<request>"


def _validation_error_items(exc: RequestValidationError) -> list[dict[str, str]]:
    """Convert validation errors to a sanitized field-level contract.

    Only stable field path, error type, and a safe message are returned. Raw
    input values, internal objects, and Pydantic ``ctx`` values are excluded.
    """
    items: list[dict[str, str]] = []
    for error in exc.errors():
        items.append(
            {
                "field": _format_validation_location(error.get("loc") or ()),
                "error_type": str(error.get("type") or "validation_error"),
                "message": str(error.get("msg") or "Invalid value"),
            }
        )
    return items


def check_readiness(application: FastAPI) -> tuple[bool, dict[str, object]]:
    """Compatibility probe that still delegates to the application use case."""

    return application.state.container.system.readiness()


def create_app(
    runtime_settings: Settings = settings,
    application_container: ApplicationContainer | None = None,
) -> FastAPI:
    configure_logging(runtime_settings.LOG_LEVEL)
    logger = logging.getLogger(__name__)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        runtime = None
        try:
            if application_container is None:
                runtime = _build_runtime(runtime_settings)
                container = runtime.container
            else:
                container = application_container
            application.state.container = container
            yield
        finally:
            try:
                del application.state.container
            except AttributeError:
                pass
            if runtime is not None:
                runtime.close()

    application = FastAPI(
        title=runtime_settings.APP_NAME,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    install_offline_api_docs(application)
    application.extra["runtime_settings"] = runtime_settings
    application.include_router(api_v1_router, prefix=runtime_settings.API_V1_PREFIX)

    @application.middleware("http")
    async def request_trace_middleware(request: Request, call_next):
        trace_id = create_trace_id(request.headers.get("X-Request-ID"))
        token = set_trace_id(trace_id)
        request.state.trace_id = trace_id
        started_at = perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = trace_id
            logger.info(
                "request_completed",
                extra={
                    "trace_id": trace_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                },
            )
            return response
        finally:
            reset_trace_id(token)

    @application.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(
                status_code=exc.status_code,
                content=error_response(
                    message=str(exc.detail.get("message", exc.detail)),
                    code=exc.status_code,
                    data=exc.detail,
                ),
            )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(message=str(exc.detail), code=exc.status_code),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=error_response(
                message="Validation error",
                code=422,
                data=_validation_error_items(exc),
            ),
        )

    @application.exception_handler(UnsupportedSchemaVersion)
    async def unsupported_schema_version_handler(request: Request, exc: UnsupportedSchemaVersion):
        logger.warning(
            "unsupported_schema_version schema_type=%s version=%s",
            exc.schema_type,
            exc.version,
        )
        return JSONResponse(
            status_code=422,
            content=error_response(
                message=str(exc),
                code=422,
                data={
                    "error_code": "unsupported_schema_version",
                    "schema_type": exc.schema_type,
                    "version": exc.version,
                },
            ),
        )

    def _upstream_error(request: Request, exc, *, include_trace: bool) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", create_trace_id())
        details = {"error_code": exc.error_code, "upstream": exc.details}
        if include_trace:
            details["upstream_trace_id"] = exc.trace_id
        return JSONResponse(
            status_code=exc.status_code,
            headers={"X-Request-ID": trace_id},
            content={
                "code": exc.status_code,
                "message": str(exc),
                "data": None,
                "details": details,
                "trace_id": trace_id,
            },
        )

    @application.exception_handler(KnowledgeGraphError)
    async def knowledge_graph_error_handler(request: Request, exc: KnowledgeGraphError):
        return _upstream_error(request, exc, include_trace=True)

    @application.exception_handler(EmergingDiscoveryError)
    async def emerging_discovery_error_handler(request: Request, exc: EmergingDiscoveryError):
        return _upstream_error(request, exc, include_trace=False)

    @application.exception_handler(PermissionDenied)
    async def permission_denied_handler(request: Request, exc: PermissionDenied):
        return JSONResponse(status_code=403, content=error_response(message=str(exc), code=403))

    @application.exception_handler(KnowledgeGraphIntegrationDisabled)
    async def knowledge_graph_disabled_handler(request: Request, exc: KnowledgeGraphIntegrationDisabled):
        return JSONResponse(status_code=503, content=error_response(message=str(exc), code=503))

    @application.exception_handler(KnowledgeGraphIntegrationNotFound)
    async def knowledge_graph_not_found_handler(request: Request, exc: KnowledgeGraphIntegrationNotFound):
        return JSONResponse(status_code=404, content=error_response(message=str(exc), code=404))

    @application.exception_handler(KnowledgeGraphIntegrationConflict)
    async def knowledge_graph_conflict_handler(request: Request, exc: KnowledgeGraphIntegrationConflict):
        return JSONResponse(status_code=409, content=error_response(message=str(exc), code=409))

    @application.exception_handler(KnowledgeGraphIntegrationRuleViolation)
    async def knowledge_graph_rule_handler(request: Request, exc: KnowledgeGraphIntegrationRuleViolation):
        return JSONResponse(status_code=422, content=error_response(message=str(exc), code=422))

    @application.exception_handler(NoReleasedJDFacts)
    async def no_released_jd_facts_handler(request: Request, exc: NoReleasedJDFacts):
        message = str(exc)
        error_code = (
            "DISCOVERY_DATASET_NOT_READY"
            if message.startswith(
                "Discovery dataset is unavailable or has no approved facts:"
            )
            else "DISCOVERY_INPUT_UNAVAILABLE"
        )
        return JSONResponse(
            status_code=422,
            content=error_response(
                message=message,
                code=422,
                data={"error_code": error_code, "message": message},
            ),
        )

    @application.exception_handler(ProjectionConflict)
    async def projection_conflict_handler(request: Request, exc: ProjectionConflict):
        return JSONResponse(status_code=409, content=error_response(message=str(exc), code=409))

    @application.exception_handler(ExternalGatewayError)
    async def external_gateway_error_handler(request: Request, exc: ExternalGatewayError):
        trace_id = getattr(request.state, "trace_id", create_trace_id())
        return JSONResponse(
            status_code=exc.status_code,
            headers={"X-Request-ID": trace_id},
            content={
                "code": exc.status_code,
                "message": str(exc),
                "data": None,
                "details": {"error_code": exc.error_code, "upstream": exc.details},
                "trace_id": trace_id,
            },
        )

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        trace_id = getattr(request.state, "trace_id", create_trace_id())
        logger.exception("Unhandled request error trace_id=%s", trace_id, exc_info=exc)
        return JSONResponse(
            status_code=500,
            headers={"X-Request-ID": trace_id},
            content=error_response(message="Internal server error", code=500, trace_id=trace_id),
        )

    @application.get("/health")
    def health_check():
        return success_response(data={"status": "ok"})

    @application.get("/readiness")
    def readiness_check():
        ready, data = check_readiness(application)
        if ready:
            return success_response(data=data)
        return JSONResponse(
            status_code=503,
            content=error_response(message="Service is not ready", code=503, data=data),
        )

    return application


app = create_app()
