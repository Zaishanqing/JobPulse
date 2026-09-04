from __future__ import annotations
from app.domain.json_types import FrozenJsonObject

from dataclasses import dataclass
from datetime import date

from app.domain.values import FrozenDict, freeze


FROZEN_DISCOVERY_DATASET_ID = "d5-short-window-main-v1-37585b4079dd"


@dataclass(frozen=True)
class Actor:
    actor_id: str
    role: str


@dataclass(frozen=True)
class ReleasedJDFact:
    source_fact_id: str
    source_fact_version: str
    jd_id: str
    title: str
    source_name: str | None
    publish_date: date | None
    structured_data: FrozenDict[str, object] | FrozenJsonObject
    content_hash: str | None = None
    source_record_id: str | None = None
    bundle_id: str | None = None
    date_source: str | None = None
    review_status: str = "published"
    consumption_path: str | None = "published"
    schema_version: str = "v2"

    def __post_init__(self) -> None:
        object.__setattr__(self, "structured_data", freeze(self.structured_data))

__all__ = ["Actor", "FROZEN_DISCOVERY_DATASET_ID", "ReleasedJDFact"]
