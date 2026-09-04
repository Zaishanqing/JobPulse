"""HTTP request tracing, safe logging, metrics and admission control."""

from __future__ import annotations

import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.infrastructure.metrics import MetricsRegistry
from app.infrastructure.structured_logging import StructuredLogger, safe_identifier


class ApiRuntimeState:
    def __init__(self) -> None:
        self.accepting_requests = True
        self.active_requests = 0


class ObservabilityMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: object,
        *,
        metrics: MetricsRegistry,
        logger: StructuredLogger,
        runtime: ApiRuntimeState,
    ) -> None:
        super().__init__(app)
        self._metrics = metrics
        self._logger = logger
        self._runtime = runtime

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = safe_identifier(
            request.headers.get("X-Request-ID") or str(uuid.uuid4())
        )
        correlation_id = safe_identifier(
            request.headers.get("X-Correlation-ID") or request_id
        )
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id
        if not self._runtime.accepting_requests and request.url.path != "/health/live":
            return JSONResponse(
                status_code=503,
                content={"code": "SERVICE_SHUTTING_DOWN", "message": "service is shutting down"},
                headers={"X-Request-ID": request_id, "X-Correlation-ID": correlation_id},
            )
        self._runtime.active_requests += 1
        started = time.perf_counter()
        response: Response | None = None
        error_code: str | None = None
        try:
            response = await call_next(request)
            return response
        except Exception:
            error_code = "UNHANDLED_REQUEST_ERROR"
            if getattr(request.app.state, "persistence_provider", None) == "postgres":
                self._metrics.increment(
                    "matching_dependency_errors_total", component="postgresql"
                )
            raise
        finally:
            duration = time.perf_counter() - started
            self._runtime.active_requests -= 1
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            status = response.status_code if response is not None else 500
            self._metrics.increment(
                "matching_http_requests_total",
                method=request.method,
                path=path,
                status=str(status),
            )
            self._metrics.observe(
                "matching_http_request_duration_seconds",
                duration,
                method=request.method,
                path=path,
            )
            context = getattr(request.state, "log_context", {})
            self._logger.event(
                "http_request",
                request_id=request_id,
                correlation_id=correlation_id,
                method=request.method,
                path=path,
                http_status=status,
                duration_ms=round(duration * 1000, 3),
                error_code=context.get("error_code") or error_code,
                task_id=context.get("task_id"),
                evaluation_id=context.get("evaluation_id"),
                access_scope=context.get("access_scope"),
                algorithm_version=context.get("algorithm_version"),
                config_version=context.get("config_version"),
                status=context.get("status"),
            )
            if response is not None:
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Correlation-ID"] = correlation_id
