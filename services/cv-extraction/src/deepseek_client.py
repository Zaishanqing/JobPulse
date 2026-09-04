"""Compatibility re-export of the shared DeepSeek client."""

from tenacity import wait_exponential

from jobgraph_contracts.deepseek import (
    DeepSeekClient,
    DeepSeekResult,
    DeepSeekTimeoutError,
    InvalidJSONError,
    MissingAPIKeyError,
)

__all__ = [
    "DeepSeekClient",
    "DeepSeekResult",
    "DeepSeekTimeoutError",
    "InvalidJSONError",
    "MissingAPIKeyError",
    "wait_exponential",
]
