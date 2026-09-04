"""Compatibility re-exports for the neutral normalization registry."""

from app.contracts.jd.normalization_registry import (
    CURRENT_NORMALIZATION_VERSION,
    get_normalization_contract,
    register_normalization_contract,
    validate_normalization,
)

__all__ = [
    "CURRENT_NORMALIZATION_VERSION", "get_normalization_contract",
    "register_normalization_contract", "validate_normalization",
]
