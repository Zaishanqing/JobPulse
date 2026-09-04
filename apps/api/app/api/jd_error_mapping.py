from fastapi import HTTPException

from app.contexts.jd_lifecycle import JDApplicationError


JD_ERROR_STATUS = {
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "invalid": 422,
}


def jd_http_exception(exc: JDApplicationError) -> HTTPException:
    return HTTPException(
        status_code=JD_ERROR_STATUS[exc.error_code],
        detail=exc.detail,
    )
