from __future__ import annotations

from pathlib import Path

from jobgraph_contracts.source_identity import build_source_key


def build_document_id(
    source_platform: str,
    source_record_id: str,
    source_version: str,
) -> str:
    """Build a readable ID for one source-record version."""
    if not isinstance(source_version, str) or not source_version.strip():
        raise ValueError("source_version must be non-empty")
    return f"{build_source_key(source_platform, source_record_id)}:{source_version.strip()}"


def build_offline_document_id(
    source_platform: str,
    input_path: str | Path,
    row_index: int,
    raw_text: str,
) -> str:
    """Build a stable source-version ID for one immutable offline input row."""
    if not source_platform.strip():
        raise ValueError("source_platform must not be empty.")
    if row_index < 1:
        raise ValueError("row_index must be positive.")
    path = Path(input_path)
    if not path.is_file():
        raise ValueError(f"Offline input file does not exist: {path}")
    source_record_id = f"{path.name}:row:{row_index}"
    return build_document_id(source_platform, source_record_id, str(row_index))
