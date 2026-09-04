"""Fake and JWT/OIDC authentication adapters."""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt

from app.domain.auth import AuthContext, derive_access_scope
from app.ports.authentication import AuthenticationError, AuthenticationProvider


class RejectingAuthenticationProvider:
    """Secure default used when authentication was not configured."""

    def authenticate(self, credential: str) -> AuthContext:
        raise AuthenticationError("AUTHENTICATION_UNAVAILABLE", "authentication is not configured")


class FakeAuthenticationProvider:
    """Explicit deterministic adapter for tests and local development only."""

    def __init__(self, identities: Mapping[str, AuthContext]) -> None:
        self._identities = dict(identities)

    def authenticate(self, credential: str) -> AuthContext:
        context = self._identities.get(credential)
        if context is None:
            raise AuthenticationError("INVALID_TOKEN", "credential is invalid")
        if context.expires_at <= datetime.now(timezone.utc):
            raise AuthenticationError("TOKEN_EXPIRED", "credential has expired")
        try:
            expected_scope = derive_access_scope(
                context.subject_id, context.tenant_id, context.roles
            )
        except ValueError as exc:
            raise AuthenticationError(
                "TOKEN_CLAIMS_INVALID", "credential claims are invalid"
            ) from exc
        if context.access_scope != expected_scope:
            raise AuthenticationError(
                "TOKEN_CLAIMS_INVALID", "credential claims are inconsistent"
            )
        return context


class JwtOidcAuthenticationProvider:
    """Validate bearer JWTs against configured OIDC issuer, audience and key."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        verification_key: str,
        algorithms: tuple[str, ...] = ("HS256",),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not issuer or not audience or not verification_key:
            raise ValueError("issuer, audience and verification_key are required")
        self._issuer = issuer
        self._audience = audience
        self._key = verification_key
        self._algorithms = algorithms
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def authenticate(self, credential: str) -> AuthContext:
        try:
            claims = jwt.decode(
                credential,
                self._key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["sub", "tenant_id", "roles", "jti", "exp", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("TOKEN_EXPIRED", "credential has expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthenticationError(
                "TOKEN_AUDIENCE_INVALID", "credential audience is invalid"
            ) from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthenticationError(
                "TOKEN_ISSUER_INVALID", "credential issuer is invalid"
            ) from exc
        except jwt.InvalidSignatureError as exc:
            raise AuthenticationError(
                "TOKEN_SIGNATURE_INVALID", "credential signature is invalid"
            ) from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationError("INVALID_TOKEN", "credential is invalid") from exc
        return self._context(claims)

    def _context(self, claims: Mapping[str, Any]) -> AuthContext:
        try:
            roles_claim = claims["roles"]
            if not isinstance(roles_claim, list) or not roles_claim or not all(
                isinstance(item, str) and item.strip() for item in roles_claim
            ):
                raise ValueError("roles must be a non-empty string array")
            roles = frozenset(roles_claim)
            subject_id = str(claims["sub"])
            tenant_id = str(claims["tenant_id"])
            scope = derive_access_scope(subject_id, tenant_id, roles)
            context = AuthContext(
                subject_id=subject_id,
                tenant_id=tenant_id,
                roles=roles,
                access_scope=scope,
                token_id=str(claims["jti"]),
                expires_at=datetime.fromtimestamp(float(claims["exp"]), timezone.utc),
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise AuthenticationError(
                "TOKEN_CLAIMS_INVALID", "credential claims are invalid"
            ) from exc
        if context.expires_at <= self._clock():
            raise AuthenticationError("TOKEN_EXPIRED", "credential has expired")
        return context


class OidcJwksAuthenticationProvider(JwtOidcAuthenticationProvider):
    """OIDC discovery/JWKS validator with bounded caching and safe key rotation."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        discovery_url: str | None = None,
        algorithms: tuple[str, ...] = ("RS256",),
        timeout_seconds: float = 5.0,
        cache_ttl_seconds: float = 300.0,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if not issuer or not audience or not algorithms:
            raise ValueError("issuer, audience and algorithms are required")
        if timeout_seconds <= 0 or cache_ttl_seconds <= 0:
            raise ValueError("OIDC timeout and cache TTL must be positive")
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._discovery_url = discovery_url or (
            f"{self._issuer}/.well-known/openid-configuration"
        )
        self._algorithms = algorithms
        self._timeout = timeout_seconds
        self._cache_ttl = cache_ttl_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._keys: dict[str, Any] = {}
        self._cache_expires_at = 0.0
        self._jwks_uri: str | None = None
        self._lock = threading.Lock()

    def authenticate(self, credential: str) -> AuthContext:
        try:
            header = jwt.get_unverified_header(credential)
        except jwt.PyJWTError as exc:
            raise AuthenticationError("INVALID_TOKEN", "credential is invalid") from exc
        kid = header.get("kid")
        algorithm = header.get("alg")
        if not isinstance(kid, str) or not kid or algorithm not in self._algorithms:
            raise AuthenticationError(
                "TOKEN_CLAIMS_INVALID", "credential header is invalid"
            )
        key = self._key_for(kid)
        try:
            claims = self._decode_mapped(credential, key)
        except AuthenticationError as exc:
            if exc.code != "TOKEN_SIGNATURE_INVALID":
                raise
            # A provider may rotate key material while retaining a kid. Refresh once.
            key = self._key_for(kid, force_refresh=True)
            claims = self._decode_mapped(credential, key)

        return self._context(claims)

    def _decode_mapped(self, credential: str, key: Any) -> Mapping[str, Any]:
        try:
            return self._decode(credential, key)
        except jwt.InvalidSignatureError as exc:
            raise AuthenticationError(
                "TOKEN_SIGNATURE_INVALID", "credential signature is invalid"
            ) from exc
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("TOKEN_EXPIRED", "credential has expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthenticationError(
                "TOKEN_AUDIENCE_INVALID", "credential audience is invalid"
            ) from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthenticationError(
                "TOKEN_ISSUER_INVALID", "credential issuer is invalid"
            ) from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationError("INVALID_TOKEN", "credential is invalid") from exc

    def check_health(self) -> None:
        self._refresh_keys(force=True)

    def _decode(self, credential: str, key: Any) -> Mapping[str, Any]:
        return jwt.decode(
            credential,
            key,
            algorithms=list(self._algorithms),
            audience=self._audience,
            issuer=self._issuer,
            options={"require": ["sub", "tenant_id", "roles", "jti", "exp", "iss", "aud"]},
        )

    def _key_for(self, kid: str, *, force_refresh: bool = False) -> Any:
        now = self._monotonic()
        if not force_refresh and now < self._cache_expires_at and kid in self._keys:
            return self._keys[kid]
        self._refresh_keys(force=force_refresh or kid not in self._keys)
        key = self._keys.get(kid)
        if key is None:
            raise AuthenticationError(
                "TOKEN_SIGNATURE_INVALID", "credential signing key is unknown"
            )
        return key

    def _refresh_keys(self, *, force: bool) -> None:
        with self._lock:
            now = self._monotonic()
            if not force and now < self._cache_expires_at:
                return
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    discovery = client.get(
                        self._discovery_url, headers={"Accept": "application/json"}
                    )
                    discovery.raise_for_status()
                    metadata = discovery.json()
                    if not isinstance(metadata, dict):
                        raise ValueError("discovery response is not an object")
                    if metadata.get("issuer") != self._issuer:
                        raise ValueError("discovery issuer mismatch")
                    jwks_uri = metadata.get("jwks_uri")
                    if not isinstance(jwks_uri, str) or not jwks_uri:
                        raise ValueError("discovery jwks_uri is missing")
                    response = client.get(jwks_uri, headers={"Accept": "application/json"})
                    response.raise_for_status()
                    payload = response.json()
                raw_keys = payload.get("keys") if isinstance(payload, dict) else None
                if not isinstance(raw_keys, list):
                    raise ValueError("JWKS keys are missing")
                keys: dict[str, Any] = {}
                for raw_key in raw_keys:
                    if not isinstance(raw_key, dict):
                        continue
                    kid = raw_key.get("kid")
                    algorithm = raw_key.get("alg")
                    if (
                        isinstance(kid, str)
                        and kid
                        and (algorithm is None or algorithm in self._algorithms)
                    ):
                        keys[kid] = jwt.PyJWK.from_dict(raw_key).key
                if not keys:
                    raise ValueError("JWKS contains no compatible keys")
                max_age = self._cache_max_age(response.headers.get("Cache-Control", ""))
            except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
                raise AuthenticationError(
                    "AUTHENTICATION_UNAVAILABLE", "OIDC provider is unavailable"
                ) from exc
            self._keys = keys
            self._jwks_uri = jwks_uri
            self._cache_expires_at = now + min(self._cache_ttl, max_age)

    def _cache_max_age(self, cache_control: str) -> float:
        match = re.search(r"(?:^|,)\s*max-age=(\d+)", cache_control, re.IGNORECASE)
        return float(match.group(1)) if match else self._cache_ttl


def build_authentication_provider(
    env: Mapping[str, str] | None = None,
) -> AuthenticationProvider:
    values = os.environ if env is None else env
    mode = values.get("MATCHING_AUTH_MODE", "required").strip().lower()
    if mode == "required":
        return RejectingAuthenticationProvider()
    if mode == "fake":
        roles = frozenset(
            item.strip()
            for item in values.get("MATCHING_FAKE_AUTH_ROLES", "candidate").split(",")
            if item.strip()
        )
        subject = values.get("MATCHING_FAKE_AUTH_SUBJECT", "test-user")
        tenant = values.get("MATCHING_FAKE_AUTH_TENANT", "test-tenant")
        context = AuthContext(
            subject_id=subject,
            tenant_id=tenant,
            roles=roles,
            access_scope=derive_access_scope(subject, tenant, roles),
            token_id="fake-development-token-id",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        return FakeAuthenticationProvider(
            {values.get("MATCHING_FAKE_AUTH_TOKEN", "test-token"): context}
        )
    if mode not in {"jwt", "oidc"}:
        raise ValueError(
            "MATCHING_AUTH_MODE must be required, fake, jwt or oidc"
        )
    algorithms = tuple(
        item.strip()
        for item in values.get("MATCHING_AUTH_ALGORITHMS", "HS256").split(",")
        if item.strip()
    )
    common = {
        "issuer": values.get("MATCHING_AUTH_ISSUER", ""),
        "audience": values.get("MATCHING_AUTH_AUDIENCE", ""),
        "algorithms": algorithms,
    }
    if mode == "oidc":
        return OidcJwksAuthenticationProvider(
            **common,
            discovery_url=values.get("MATCHING_AUTH_DISCOVERY_URL") or None,
            timeout_seconds=float(values.get("MATCHING_AUTH_TIMEOUT_SECONDS", "5")),
            cache_ttl_seconds=float(values.get("MATCHING_AUTH_JWKS_CACHE_TTL_SECONDS", "300")),
        )
    return JwtOidcAuthenticationProvider(
        **common,
        verification_key=values.get("MATCHING_AUTH_VERIFICATION_KEY", ""),
    )
