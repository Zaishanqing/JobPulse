from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from jobgraph_contracts.base import StrictContract


class ConfusablePositionV3(StrictContract):
    """A versioned ambiguity edge with the authoritative distinction rule."""

    position_code: str = Field(min_length=1, max_length=100)
    distinguish_by: str = Field(min_length=1)


class ResolvedPositionCatalogItemV3(StrictContract):
    main_system_position_id: str = Field(min_length=1, max_length=100)
    position_code: str = Field(min_length=1, max_length=100)
    position_name: str = Field(min_length=1, max_length=150)
    family_code: str = Field(min_length=1, max_length=80)
    family_name: str = Field(min_length=1, max_length=120)
    definition: str = Field(min_length=1)
    aliases: list[str]
    include_when: list[str]
    exclude_when: list[str]
    confusable_with: list[ConfusablePositionV3]
    lifecycle_status: Literal["active", "deprecated"]
    deprecated_at: str | None
    replaced_by: str | None
    sample_support_status: Literal["none", "sparse", "sufficient"]

    @model_validator(mode="after")
    def validate_confusable_positions(self) -> "ResolvedPositionCatalogItemV3":
        codes = [item.position_code for item in self.confusable_with]
        if self.position_code in codes:
            raise ValueError("position cannot be confusable with itself")
        if len(codes) != len(set(codes)):
            raise ValueError("confusable_with position_code must be unique")
        return self


class ResolvedPositionCatalogV3(StrictContract):
    schema_version: Literal["resolved-position-catalog.v3"]
    taxonomy_version: Literal["position-taxonomy.v3.0.0"]
    position_count: int = Field(ge=1)
    positions: list[ResolvedPositionCatalogItemV3] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_snapshot(self) -> "ResolvedPositionCatalogV3":
        if self.position_count != len(self.positions):
            raise ValueError("position_count does not match positions")
        main_ids = [item.main_system_position_id for item in self.positions]
        codes = [item.position_code for item in self.positions]
        if len(main_ids) != len(set(main_ids)):
            raise ValueError("main_system_position_id must be unique")
        if len(codes) != len(set(codes)):
            raise ValueError("position_code must be unique")
        known_codes = set(codes)
        unknown_confusable_codes = sorted(
            {
                edge.position_code
                for item in self.positions
                for edge in item.confusable_with
                if edge.position_code not in known_codes
            }
        )
        if unknown_confusable_codes:
            raise ValueError(
                "confusable_with references unknown position_code: "
                + ", ".join(unknown_confusable_codes)
            )
        return self


def build_resolved_position_catalog_v3(
    positions: list[dict[str, object]],
) -> ResolvedPositionCatalogV3:
    items = [
        ResolvedPositionCatalogItemV3.model_validate(item)
        for item in positions
    ]
    return ResolvedPositionCatalogV3(
        schema_version="resolved-position-catalog.v3",
        taxonomy_version="position-taxonomy.v3.0.0",
        position_count=len(items),
        positions=items,
    )
