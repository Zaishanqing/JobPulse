from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jobgraph_contracts.crawler_jd import CrawlerJDEnvelopeV1


@dataclass(frozen=True)
class ExportBatchRecord:
    publication_id: str
    envelope: CrawlerJDEnvelopeV1


@dataclass(frozen=True)
class ExportSummary:
    batch_id: str
    bundle_id: str
    output_path: Path
    record_count: int
