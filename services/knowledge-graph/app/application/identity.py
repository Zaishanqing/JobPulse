from dataclasses import dataclass

from app.domain.identity import (
    VALID_ROLES,
    IdentityActor,
    Permission,
    has_permission,
)
from app.ports.identity import IdentityRepository, PasswordVerifier, TokenCodec


class AuthenticationFailed(Exception):
    pass


class AuthorizationDenied(Exception):
    pass


@dataclass(frozen=True)
class AccessToken:
    value: str
    role: str


@dataclass(frozen=True)
class IntegrationIdentity:
    main_user_id: str | None
    main_user_role: str | None


@dataclass(frozen=True)
class IdentityService:
    identities: IdentityRepository
    passwords: PasswordVerifier
    tokens: TokenCodec
    service_username: str

    def login(self, username: str, password: str) -> AccessToken:
        record = self.identities.by_username(username)
        if record is None or not self.passwords.verify(password, record.password_hash):
            raise AuthenticationFailed("invalid credentials")
        actor = IdentityActor(record.user_id, record.username, record.role)
        return AccessToken(self.tokens.encode(actor), actor.role)

    def current_actor(self, token: str) -> IdentityActor:
        try:
            user_id = self.tokens.decode_subject(token)
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthenticationFailed("invalid token") from exc
        record = self.identities.by_id(user_id)
        if record is None or record.role not in VALID_ROLES:
            raise AuthenticationFailed("user not found")
        return IdentityActor(record.user_id, record.username, record.role)

    @staticmethod
    def authorize(actor: IdentityActor, permission: Permission) -> IdentityActor:
        if not has_permission(actor, permission):
            raise AuthorizationDenied("insufficient permission")
        return actor

    def integration_identity(
        self,
        actor: IdentityActor,
        main_user_id: str | None,
        main_user_role: str | None,
        *,
        required: bool = False,
    ) -> IntegrationIdentity | None:
        if actor.username != self.service_username or actor.role != "integration_service":
            if required:
                raise AuthorizationDenied(
                    "published JD facts require the main-system service account"
                )
            return None
        return IntegrationIdentity(main_user_id, main_user_role)
