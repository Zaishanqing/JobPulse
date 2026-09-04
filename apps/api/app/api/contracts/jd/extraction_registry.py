"""Compatibility re-exports for the neutral extraction registry."""

from app.contracts.jd.extraction_registry import (
    CURRENT_EXTRACTION_VERSION,
    get_extraction_contract,
    register_extraction_contract,
    validate_extraction,
)

__all__ = [
    "CURRENT_EXTRACTION_VERSION", "get_extraction_contract",
    "register_extraction_contract", "validate_extraction",
]
