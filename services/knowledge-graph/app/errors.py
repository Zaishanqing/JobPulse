import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.application.errors import ApplicationError

logger = logging.getLogger(__name__)

def _body(request: Request, code: int, message: str, details: dict | list | None = None):
    return {"code": code, "message": message, "data": None, "details": details or {}, "trace_id": getattr(request.state, "trace_id", "req_unknown")}

def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, exc: ApplicationError):
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(request, exc.status_code * 100 + 2, str(exc), exc.details),
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        details = exc.detail if isinstance(exc.detail, dict) else {}
        message = details.get("message", "request failed") if details else str(exc.detail)
        return JSONResponse(status_code=exc.status_code, content=_body(request, exc.status_code * 100 + 1, message, details))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        for error in errors:
            context = error.get("ctx")
            if isinstance(context, dict):
                error["ctx"] = {
                    key: str(value) if isinstance(value, Exception) else value
                    for key, value in context.items()
                }
        return JSONResponse(status_code=422, content=_body(request, 42201, "request validation failed", {"errors": errors}))

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError):
        logger.info("database constraint rejected request trace_id=%s", getattr(request.state, "trace_id", None))
        return JSONResponse(status_code=409, content=_body(request, 40901, "database constraint rejected the operation"))

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        logger.exception("database error trace_id=%s", getattr(request.state, "trace_id", None))
        return JSONResponse(status_code=500, content=_body(request, 50001, "internal server error"))

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        logger.exception("unhandled error trace_id=%s", getattr(request.state, "trace_id", None))
        return JSONResponse(status_code=500, content=_body(request, 50000, "internal server error"))
