from __future__ import annotations

import hashlib

from jose import jwe


def encrypt_api_key(api_key: str, secret_key: str) -> str:
    key = hashlib.sha256(secret_key.encode("utf-8")).digest()
    token = jwe.encrypt(
        api_key.encode("utf-8"),
        key,
        algorithm="dir",
        encryption="A256GCM",
    )
    return token.decode("utf-8") if isinstance(token, bytes) else token


def decrypt_api_key(ciphertext: str, secret_key: str) -> str:
    key = hashlib.sha256(secret_key.encode("utf-8")).digest()
    value = jwe.decrypt(ciphertext, key)
    return value.decode("utf-8")
