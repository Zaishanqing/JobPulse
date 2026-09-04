from dataclasses import dataclass
from typing import Protocol

from app.domain.identity import IdentityActor


@dataclass(frozen=True)
class IdentityRecord:
    user_id: int
    username: str
    role: str
    password_hash: str


class IdentityRepository(Protocol):
    def by_id(self, user_id: int) -> IdentityRecord | None: ...
    def by_username(self, username: str) -> IdentityRecord | None: ...


class PasswordVerifier(Protocol):
    def verify(self, plain_text: str, password_hash: str) -> bool: ...


class TokenCodec(Protocol):
    def encode(self, actor: IdentityActor) -> str: ...
    def decode_subject(self, token: str) -> int: ...
