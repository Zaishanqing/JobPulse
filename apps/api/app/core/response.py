from app.core.request_context import get_trace_id


def success_response(
    data=None,
    message: str = "success",
    trace_id: str | None = None,
    details=None,
) -> dict:
    response = {
        "code": 0,
        "message": message,
        "data": data,
        "trace_id": trace_id or get_trace_id(),
    }
    if details is not None:
        response["details"] = details
    return response


def error_response(
    message: str,
    code: int,
    trace_id: str | None = None,
    data=None,
) -> dict:
    return {
        "code": code,
        "message": message,
        "data": data,
        "trace_id": trace_id or get_trace_id(),
    }
