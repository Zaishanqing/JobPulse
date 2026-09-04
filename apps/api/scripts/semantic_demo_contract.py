"""Load the repository's fixed semantic-demo contract without duplicating values."""

from __future__ import annotations

import os
from pathlib import Path


CONTRACT_PATH = Path(__file__).resolve().parents[1] / "config" / "semantic-demo-contract.env"
REPOSITORY_ENV_PATH = CONTRACT_PATH.parents[1] / ".env"
REQUIRED_KEYS = (
    "EMBEDDING_MODEL_ID",
    "EMBEDDING_MODEL_REVISION",
    "EMBEDDING_DIMENSION",
    "EMBEDDING_NORMALIZED",
    "EMBEDDING_REPRESENTATION",
    "EMBEDDING_SIMILARITY",
    "MATCHING_SEMANTIC_MODE",
    "MATCHING_VECTOR_EMBEDDING_MODEL",
    "MATCHING_VECTOR_EMBEDDING_REVISION",
    "MATCHING_QDRANT_DIMENSION",
    "MATCHING_QDRANT_COLLECTION",
    "MATCHING_VECTOR_INDEX_REVISION",
    "MATCHING_VECTOR_TEXT_DERIVATION_VERSION",
)


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    for key in REQUIRED_KEYS:
        if not values.get(key):
            raise ValueError(f"semantic demo contract is missing {key}: {path}")
    return values


def apply_contract_environment(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ.setdefault(key, value)


def apply_repository_environment(path: Path = REPOSITORY_ENV_PATH) -> None:
    """Load local Compose variables without overriding explicit process values."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


__all__ = [
    "CONTRACT_PATH",
    "REPOSITORY_ENV_PATH",
    "apply_contract_environment",
    "apply_repository_environment",
    "load_contract",
]
