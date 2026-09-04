"""Deprecated compatibility exports for historical Python callers.

HTTP routes use the account application use cases.  This module deliberately owns
no authentication rule, ORM query, transaction, framework exception, or setting.
"""

from app.infrastructure.accounts import (
    hash_password_compat as hash_password,
    register_user_compat as register_user,
)

__all__ = ["hash_password", "register_user"]
