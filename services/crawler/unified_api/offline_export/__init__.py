"""Offline JD bundle production from the crawler's local MySQL database."""

from unified_api.offline_export.contracts import (
    ExportBatchRecord,
    ExportSummary,
)

__all__ = ["ExportBatchRecord", "ExportSummary"]
