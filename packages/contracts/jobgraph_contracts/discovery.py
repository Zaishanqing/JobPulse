from datetime import date
from typing import Any, Literal

from pydantic import Field, field_validator

from jobgraph_contracts.base import StrictContract


class DiscoveryJDSnapshotV2(StrictContract):
    source_fact_id: str = Field(min_length=1)
    source_fact_version: str = Field(min_length=1)
    jd_id: str
    schema_version: Literal["v2"]
    review_status: Literal["approved", "reviewed", "published"]
    consumption_path: Literal["legacy_reviewed", "published"] | None = None
    title: str = Field(min_length=1)
    source_name: str | None = None
    publish_date: date | None = None
    structured_data: dict[str, Any]

    @field_validator("structured_data")
    @classmethod
    def validate_structured_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        required = {
            "responsibilities",
            "required_skills",
            "bonus_skills",
            "business_scenarios",
        }
        missing = sorted(required - value.keys())
        if missing:
            raise ValueError(
                "structured_data is missing required fields: " + ", ".join(missing)
            )
        if any(not isinstance(value[field], list) for field in required):
            raise ValueError("structured_data list fields must be arrays")
        return value
