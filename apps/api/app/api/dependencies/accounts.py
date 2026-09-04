from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer

from app.contexts.access import (
    AccountHandlers,
    InvalidCredentials,
)
from app.api.dependencies.container import get_application_container
from app.domain.accounts import AccountActor
from app.contexts.access import AccountRecord


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_account_handlers_from_request(request: Request) -> AccountHandlers:
    return get_application_container(request).accounts


def get_authenticated_account(
    token: str = Depends(oauth2_scheme),
    handlers: AccountHandlers = Depends(get_account_handlers_from_request),
) -> AccountRecord:
    try:
        return handlers.authentication.resolve(token)
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def get_account_actor(
    account: AccountRecord = Depends(get_authenticated_account),
) -> AccountActor:
    return AccountActor(account.account_id, account.role)
