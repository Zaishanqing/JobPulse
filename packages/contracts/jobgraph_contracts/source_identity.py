"""Shared source identity helpers.

Identity is made from explicit source fields.  Content is not an identity
mechanism; versioning belongs to the source record or database version row.
"""

from __future__ import annotations

import hashlib
from datetime import datetime


# -- source / version keys -------------------------------------------------

def validate_source_platform(platform: str) -> None:
    """Validate the external platform label at the contract boundary."""
    if not isinstance(platform, str) or not platform.strip():
        raise ValueError("source_platform must be non-empty")
    if len(platform.strip()) > 64:
        raise ValueError("source_platform is too long")


# -- source_record_id normalisation ----------------------------------------


def normalize_source_record_id(source_record_id: str) -> str:
    """Strip leading/trailing whitespace and reject empty ids.

    Internal whitespace is preserved unchanged.  This function MUST be used
    by every entry point that consumes a source record identifier so that
    identity keys are always built from the same normalised form.
    """
    if not isinstance(source_record_id, str):
        raise TypeError(f"source_record_id must be str; got {type(source_record_id)}")
    normalized = source_record_id.strip()
    if not normalized:
        raise ValueError("source_record_id must be non-empty after stripping whitespace")
    return normalized


# -- timezone validation ---------------------------------------------------


def ensure_timezone_aware(value: datetime) -> None:
    """Raise :class:`ValueError` when *value* is a naive datetime.

    All cross-service timestamps MUST carry an explicit timezone so that
    comparisons and serialisation are unambiguous.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"datetime must be timezone-aware; got {value!r}"
        )


# -- key builders ----------------------------------------------------------


def build_source_key(source_platform: str, source_record_id: str) -> str:
    """Return an unambiguous source identity key.

    The resulting key is a readable composite of the explicit source fields.

    *source_record_id* is normalised (leading/trailing whitespace stripped)
    before the key is built so that an Envelope and a Bundle referring to the
    same source always produce the identical key.
    """
    validate_source_platform(source_platform)
    normalized_id = normalize_source_record_id(source_record_id)
    return f"{source_platform}:{normalized_id}"


# -- raw content fingerprint ------------------------------------------------


def compute_content_hash(raw_text: str) -> str:
    """Return the canonical raw content fingerprint for a JD body.

    The format is ``sha256:<64 lowercase hex>`` and MUST be stable across all
    producers/consumers.  The hash is computed over the UTF-8 bytes of the
    exact ``raw_text`` value.
    """
    if not isinstance(raw_text, str):
        raise TypeError(f"raw_text must be str; got {type(raw_text)}")
    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# -- crawl_time validation ---------------------------------------------------


def parse_crawl_time(value: object) -> "datetime":
    """Parse and validate a crawl timestamp.

    Returns a timezone-aware UTC datetime.  Raises ValueError on missing,
    unparseable, or naive input.  NEVER fabricates ``datetime.now()``.
    """
    from datetime import datetime as dt, timezone as tz

    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("crawl_time is required and must not be empty")
    if isinstance(value, dt):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = dt.fromisoformat(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"crawl_time {value!r} is not valid ISO 8601") from exc
    else:
        raise TypeError(f"crawl_time must be str or datetime, got {type(value)}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"crawl_time must be timezone-aware; got {value!r}")
    return parsed.astimezone(tz.utc)
