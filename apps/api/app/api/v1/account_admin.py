from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.accounts import get_account_actor, get_account_handlers_from_request
from app.contexts.access import (
    AccountHandlers,
    AccountInputError,
    AccountNotFound,
    InvalidAccountChange,
)
from app.contexts.access import AccountActiveChangeCommand, AccountRoleChangeCommand
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.schemas.auth import AccountRoleChangeRequest, PasswordChangeRequest
from app.domain.errors import PermissionDenied


router = APIRouter(tags=["accounts"])


def _raise(
    exc: AccountNotFound | PermissionDenied | InvalidAccountChange | AccountInputError,
) -> None:
    if isinstance(exc, AccountNotFound):
        code = 404
    elif isinstance(exc, PermissionDenied):
        code = 403
    elif isinstance(exc, InvalidAccountChange):
        code = 409
    else:
        code = 422
    raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.put("/auth/password")
def change_password(payload: PasswordChangeRequest, actor: AccountActor = Depends(get_account_actor), handlers: AccountHandlers = Depends(get_account_handlers_from_request)):
    try:
        data = handlers.password.execute(actor, payload.old_password, payload.new_password)
    except (AccountNotFound, AccountInputError) as exc:
        _raise(exc)
    return success_response(data=data)


@router.get("/roles")
def list_roles(actor: AccountActor = Depends(get_account_actor), handlers: AccountHandlers = Depends(get_account_handlers_from_request)):
    try:
        roles = handlers.management.list_roles(actor)
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=list(roles))


@router.get("/permissions")
def list_permissions(actor: AccountActor = Depends(get_account_actor), handlers: AccountHandlers = Depends(get_account_handlers_from_request)):
    try:
        permissions = handlers.management.list_permissions(actor)
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=list(permissions))


@router.put("/users/{user_id}/role")
def update_user_role(user_id: str, payload: AccountRoleChangeRequest = Body(default_factory=AccountRoleChangeRequest), actor: AccountActor = Depends(get_account_actor), handlers: AccountHandlers = Depends(get_account_handlers_from_request)):
    try:
        data = handlers.management.change_role(
            actor, AccountRoleChangeCommand(user_id, payload.role)
        )
    except (
        AccountNotFound,
        AccountInputError,
        InvalidAccountChange,
        PermissionDenied,
    ) as exc:
        _raise(exc)
    return success_response(data=data)


def _change_active(user_id: str, active: bool, actor: AccountActor, handlers: AccountHandlers):
    try:
        data = handlers.management.change_active(
            actor, AccountActiveChangeCommand(user_id, active)
        )
    except (AccountNotFound, InvalidAccountChange, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=data)


@router.put("/users/{user_id}/disable")
def disable_user(user_id: str, actor: AccountActor = Depends(get_account_actor), handlers: AccountHandlers = Depends(get_account_handlers_from_request)):
    return _change_active(user_id, False, actor, handlers)


@router.put("/users/{user_id}/enable")
def enable_user(user_id: str, actor: AccountActor = Depends(get_account_actor), handlers: AccountHandlers = Depends(get_account_handlers_from_request)):
    return _change_active(user_id, True, actor, handlers)
