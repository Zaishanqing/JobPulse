"""Protocol-level tests against a live local OIDC discovery/JWKS HTTP provider."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.infrastructure.authentication import OidcJwksAuthenticationProvider
from app.ports.authentication import AuthenticationError


class _OidcHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        server = self.server
        if server.unavailable:  # type: ignore[attr-defined]
            self.send_response(503)
            self.end_headers()
            return
        if self.path == "/.well-known/openid-configuration":
            payload = {
                "issuer": server.issuer,  # type: ignore[attr-defined]
                "jwks_uri": f"{server.issuer}/jwks",  # type: ignore[attr-defined]
            }
        elif self.path == "/jwks":
            payload = {"keys": list(server.keys.values())}  # type: ignore[attr-defined]
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _jwk(private_key: rsa.RSAPrivateKey, kid: str) -> dict[str, object]:
    value = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    return {**value, "kid": kid, "alg": "RS256", "use": "sig"}


@contextmanager
def _oidc_provider():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OidcHandler)
    server.issuer = f"http://127.0.0.1:{server.server_port}"  # type: ignore[attr-defined]
    server.keys = {}  # type: ignore[attr-defined]
    server.unavailable = False  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _token(
    key: rsa.RSAPrivateKey,
    kid: str,
    issuer: str,
    *,
    audience: str = "matching-api",
    roles: object = None,
    expires_delta: timedelta = timedelta(minutes=5),
) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": "candidate-opaque",
            "tenant_id": "tenant-opaque",
            "roles": ["candidate"] if roles is None else roles,
            "jti": f"token-{kid}",
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + expires_delta,
        },
        key,
        algorithm="RS256",
        headers={"kid": kid},
    )


@pytest.mark.oidc_integration
def test_live_oidc_discovery_jwks_rotation_and_cached_outage_behavior():
    with _oidc_provider() as server:
        first_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        second_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        server.keys["key-1"] = _jwk(first_key, "key-1")  # type: ignore[attr-defined]
        provider = OidcJwksAuthenticationProvider(
            issuer=server.issuer,  # type: ignore[attr-defined]
            audience="matching-api",
            timeout_seconds=1,
            cache_ttl_seconds=300,
        )

        first_token = _token(first_key, "key-1", server.issuer)  # type: ignore[attr-defined]
        assert provider.authenticate(first_token).subject_id == "candidate-opaque"

        server.keys["key-2"] = _jwk(second_key, "key-2")  # type: ignore[attr-defined]
        second_token = _token(second_key, "key-2", server.issuer)  # type: ignore[attr-defined]
        assert provider.authenticate(second_token).token_id == "token-key-2"

        replacement_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        server.keys["key-2"] = _jwk(replacement_key, "key-2")  # type: ignore[attr-defined]
        replacement_token = _token(
            replacement_key, "key-2", server.issuer  # type: ignore[attr-defined]
        )
        assert provider.authenticate(replacement_token).token_id == "token-key-2"

        server.unavailable = True  # type: ignore[attr-defined]
        assert provider.authenticate(first_token).token_id == "token-key-1"
        unknown_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(AuthenticationError) as unavailable:
            provider.authenticate(
                _token(unknown_key, "key-new", server.issuer)  # type: ignore[attr-defined]
            )
        assert unavailable.value.code == "AUTHENTICATION_UNAVAILABLE"


@pytest.mark.oidc_integration
def test_live_oidc_rejects_expiry_audience_signature_and_claim_errors():
    with _oidc_provider() as server:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        server.keys["current"] = _jwk(key, "current")  # type: ignore[attr-defined]
        provider = OidcJwksAuthenticationProvider(
            issuer=server.issuer,  # type: ignore[attr-defined]
            audience="matching-api",
            timeout_seconds=1,
        )

        cases = (
            (
                _token(
                    key,
                    "current",
                    server.issuer,  # type: ignore[attr-defined]
                    expires_delta=timedelta(seconds=-1),
                ),
                "TOKEN_EXPIRED",
            ),
            (_token(key, "current", server.issuer, audience="wrong"), "TOKEN_AUDIENCE_INVALID"),  # type: ignore[attr-defined]
            (_token(wrong_key, "current", server.issuer), "TOKEN_SIGNATURE_INVALID"),  # type: ignore[attr-defined]
            (_token(key, "current", server.issuer, roles=7), "TOKEN_CLAIMS_INVALID"),  # type: ignore[attr-defined]
        )
        for token, expected_code in cases:
            with pytest.raises(AuthenticationError) as rejected:
                provider.authenticate(token)
            assert rejected.value.code == expected_code
