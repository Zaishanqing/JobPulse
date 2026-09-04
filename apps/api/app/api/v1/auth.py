from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.accounts import (
    get_account_handlers_from_request,
    get_authenticated_account,
)
from app.contexts.access import (
    AccountInputError,
    AccountHandlers,
    DuplicateAccount,
    InvalidCredentials,
)
from app.core.response import success_response
from app.domain.accounts import AccountRuleViolation
from app.contexts.access import AccountRecord
from app.schemas.auth import LoginRequest, PublicRegisterRequest
from app.domain.permissions import permissions_for_role


router = APIRouter(prefix="/auth", tags=["auth"])


def _user_data(user: AccountRecord) -> dict:
    return {
        "user_id": user.account_id,
        "role": user.role,
        "username": user.username,
        "email": user.email,
        "phone": user.phone,
        "is_active": user.is_active,
        "permissions": list(permissions_for_role(user.role)),
    }


@router.post("/register")
def register(
    payload: PublicRegisterRequest,
    handlers: AccountHandlers = Depends(get_account_handlers_from_request),
):
    try:
        user = handlers.registration.execute(**payload.model_dump())
    except (AccountRuleViolation, AccountInputError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DuplicateAccount as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return success_response(
        data={"user_id": user.account_id, "role": user.role, "username": user.username}
    )


@router.post("/login")
def login(
    payload: LoginRequest,
    handlers: AccountHandlers = Depends(get_account_handlers_from_request),
):
    try:
        _, token = handlers.authentication.execute(payload.username, payload.password)
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return success_response(data={"access_token": token, "token_type": "bearer"})


@router.get("/me")
def me(current_user: AccountRecord = Depends(get_authenticated_account)):
    return success_response(data=_user_data(current_user))


@router.post("/logout")
def logout(current_user: AccountRecord = Depends(get_authenticated_account)):
    return success_response(data={"username": current_user.username, "logged_out": True})


@router.post("/logout-all")
def logout_all(
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: AccountHandlers = Depends(get_account_handlers_from_request),
):
    handlers.authentication.logout_all(current_user)
    return success_response(
        data={"username": current_user.username, "logged_out_all": True}
    )


@router.post("/refresh")
def refresh(
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: AccountHandlers = Depends(get_account_handlers_from_request),
):
    token = handlers.authentication.refresh(current_user)
    return success_response(data={"access_token": token, "token_type": "bearer"})
