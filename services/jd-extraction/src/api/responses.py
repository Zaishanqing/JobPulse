from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def success_response(data: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": 0, "message": "success", "data": data},
    )


def error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
    request_id: str,
    retryable: bool = False,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "message": message,
            "data": {
                "error_code": error_code,
                "retryable": retryable,
                "request_id": request_id,
            },
        },
    )
