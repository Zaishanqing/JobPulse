"""Versioned competition demo input manifest shared by all demo owners.

The manifest defines the `competition-demo-v1` dataset with logical aliases
only. No database auto-increment id, privacy data, key, or raw resume is part
of this contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from jobgraph_contracts.base import StrictContract


COMPETITION_DEMO_MANIFEST_V1 = "competition-demo-manifest.v1"
COMPETITION_DEMO_V1 = "competition-demo-v1"
DEMO_ONLY_LABEL = "demo-only"

_ALIAS_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,127}$"


class DemoOnlyCleanup(StrictContract):
    label: Literal["demo-only"] = "demo-only"
    allowed_on: Literal["non_production"] = "non_production"
    removed_by: Literal["remove-demo-only"] = "remove-demo-only"
    excluded_from_official_experiments: Literal[True] = True


class PositionFamilyRef(StrictContract):
    alias: str = Field(pattern=_ALIAS_PATTERN)
    family_name: str = Field(min_length=1, max_length=120)
    position_alias: str = Field(pattern=_ALIAS_PATTERN)


class JDInputRef(StrictContract):
    alias: str = Field(pattern=_ALIAS_PATTERN)
    source_type: str = Field(min_length=1, max_length=40)
    input_path: str = Field(min_length=1, max_length=240)
    anonymized: Literal[True] = True
    demo_only: Literal[True] = True
    expected_contract: Literal["extracted-jd-bundle.v1"] = "extracted-jd-bundle.v1"


class CVInputRef(StrictContract):
    alias: str = Field(pattern=_ALIAS_PATTERN)
    input_path: str = Field(min_length=1, max_length=240)
    anonymized: Literal[True] = True
    demo_only: Literal[True] = True
    expected_contract: Literal["cv-extraction-http.v2"] = "cv-extraction-http.v2"


class TrendWindowRef(StrictContract):
    """Half-open time range [start, end)."""

    alias: str = Field(pattern=_ALIAS_PATTERN)
    start: datetime
    end: datetime
    graph_version_alias: str = Field(pattern=_ALIAS_PATTERN)

    @model_validator(mode="after")
    def validate_window(self) -> "TrendWindowRef":
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("trend window timestamps must be timezone-aware")
        if self.start >= self.end:
            raise ValueError("trend window start must be before end")
        return self


class GraphVersionRef(StrictContract):
    alias: str = Field(pattern=_ALIAS_PATTERN)
    version_name: str = Field(min_length=1, max_length=80)
    version_number: int | None = Field(default=None, ge=1)
    position_alias: str = Field(pattern=_ALIAS_PATTERN)
    graph_version_id: int | None = Field(
        default=None,
        ge=1,
        description="assigned by KG at load time; never part of the definition",
    )


class PublishedPositionRef(StrictContract):
    alias: str = Field(pattern=_ALIAS_PATTERN)
    position_alias: str = Field(pattern=_ALIAS_PATTERN)
    graph_version_alias: str = Field(pattern=_ALIAS_PATTERN)
    profile_contract: Literal["position-profile.v2"] = "position-profile.v2"
    profile_state: Literal["published"] = "published"


class MatchingTargetRef(StrictContract):
    alias: str = Field(pattern=_ALIAS_PATTERN)
    cv_alias: str = Field(pattern=_ALIAS_PATTERN)
    position_alias: str = Field(pattern=_ALIAS_PATTERN)
    graph_version_alias: str = Field(pattern=_ALIAS_PATTERN)
    evaluation_contract: Literal[
        "matching-evaluation-result.v1"
    ] = "matching-evaluation-result.v1"


class DemoCaseRef(StrictContract):
    alias: str = Field(pattern=_ALIAS_PATTERN)
    kind: Literal["success", "insufficient_evidence"]
    expected_status: Literal[
        "succeeded", "completed", "rejected", "insufficient_evidence", "answered"
    ]
    description: str = Field(min_length=1, max_length=500)


class ExpectedResourceRef(StrictContract):
    alias: str = Field(pattern=_ALIAS_PATTERN)
    resource_type: str = Field(min_length=1, max_length=64)
    contract_version: str = Field(min_length=1, max_length=64)
    owner_service: str = Field(min_length=1, max_length=64)
    expected_status: str | None = Field(default=None, min_length=1, max_length=32)


class ExpectedRelationRef(StrictContract):
    alias: str = Field(pattern=_ALIAS_PATTERN)
    source: str = Field(pattern=_ALIAS_PATTERN)
    target: str = Field(pattern=_ALIAS_PATTERN)
    relation_type: str = Field(min_length=1, max_length=64)


class CompetitionDemoManifestV1(StrictContract):
    contract_version: Literal["competition-demo-manifest.v1"] = (
        "competition-demo-manifest.v1"
    )
    dataset_version: Literal["competition-demo-v1"] = "competition-demo-v1"
    demo_only: DemoOnlyCleanup
    implementation_status: Literal["loadable_foundation"] = "loadable_foundation"
    position_family: PositionFamilyRef
    jds: list[JDInputRef] = Field(min_length=1)
    cvs: list[CVInputRef] = Field(min_length=1)
    trend_windows: list[TrendWindowRef] = Field(min_length=3, max_length=3)
    graph_versions: list[GraphVersionRef] = Field(min_length=1)
    published_position: PublishedPositionRef
    matching_target: MatchingTargetRef
    success_case: DemoCaseRef
    insufficient_evidence_case: DemoCaseRef
    expected_resources: list[ExpectedResourceRef] = Field(min_length=1)
    relations: list[ExpectedRelationRef] = Field(min_length=1)
    created_at: datetime
    maintained_by: str = Field(min_length=1, max_length=120)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_references(self) -> "CompetitionDemoManifestV1":
        jd_aliases = [item.alias for item in self.jds]
        cv_aliases = [item.alias for item in self.cvs]
        window_aliases = [item.alias for item in self.trend_windows]
        graph_aliases = [item.alias for item in self.graph_versions]
        resource_aliases = [item.alias for item in self.expected_resources]

        if len(jd_aliases) != len(set(jd_aliases)):
            raise ValueError("JD input aliases must be unique")
        if len(cv_aliases) != len(set(cv_aliases)):
            raise ValueError("CV input aliases must be unique")
        if len(window_aliases) != len(set(window_aliases)):
            raise ValueError("trend window aliases must be unique")
        if len(graph_aliases) != len(set(graph_aliases)):
            raise ValueError("graph version aliases must be unique")
        if len(resource_aliases) != len(set(resource_aliases)):
            raise ValueError("expected resource aliases must be unique")

        starts = [item.start for item in self.trend_windows]
        if starts != sorted(starts):
            raise ValueError("trend windows must be ordered by start")
        if any(
            item.end > next_item.start
            for item, next_item in zip(self.trend_windows, self.trend_windows[1:])
        ):
            raise ValueError("trend windows must not overlap")

        for version in self.graph_versions:
            if version.position_alias != self.position_family.position_alias:
                raise ValueError(
                    "graph version must reference the manifest position"
                )

        referenced_graphs = {
            self.published_position.graph_version_alias,
            self.matching_target.graph_version_alias,
            *(item.graph_version_alias for item in self.trend_windows),
        }
        missing_graphs = referenced_graphs - set(graph_aliases)
        if missing_graphs:
            raise ValueError(
                "graph version references are missing: " + ", ".join(sorted(missing_graphs))
            )

        family_position = self.position_family.position_alias
        if self.published_position.position_alias != family_position:
            raise ValueError("published position must use the manifest position")
        if self.matching_target.position_alias != family_position:
            raise ValueError("matching target must use the manifest position")
        if self.matching_target.cv_alias not in set(cv_aliases):
            raise ValueError("matching target must reference a manifest CV")

        if self.success_case.alias == self.insufficient_evidence_case.alias:
            raise ValueError("success and insufficient evidence cases must differ")
        if self.success_case.kind != "success":
            raise ValueError("success_case must use kind=success")
        if self.success_case.expected_status != "completed":
            raise ValueError("success_case must use expected_status=completed")
        if self.insufficient_evidence_case.kind != "insufficient_evidence":
            raise ValueError("insufficient_evidence_case must use kind=insufficient_evidence")
        if (
            self.insufficient_evidence_case.expected_status
            != "insufficient_evidence"
        ):
            raise ValueError(
                "insufficient_evidence_case must use expected_status=insufficient_evidence"
            )

        relation_aliases = [item.alias for item in self.relations]
        if len(relation_aliases) != len(set(relation_aliases)):
            raise ValueError("relation aliases must be unique")

        known = {
            *jd_aliases,
            *cv_aliases,
            *window_aliases,
            *graph_aliases,
            *resource_aliases,
            self.published_position.alias,
            self.matching_target.alias,
            self.success_case.alias,
            self.insufficient_evidence_case.alias,
        }
        for relation in self.relations:
            if relation.source not in known or relation.target not in known:
                raise ValueError(
                    "relation references undefined alias: " + relation.alias
                )

        relation_endpoints = {
            *(item.source for item in self.relations),
            *(item.target for item in self.relations),
        }
        isolated_inputs = {
            *(alias for alias in jd_aliases),
            *(alias for alias in cv_aliases),
            *(alias for alias in window_aliases),
        } - relation_endpoints
        if isolated_inputs:
            raise ValueError(
                "input aliases must appear in at least one relation: "
                + ", ".join(sorted(isolated_inputs))
            )
        return self


__all__ = [
    "COMPETITION_DEMO_MANIFEST_V1",
    "COMPETITION_DEMO_V1",
    "DEMO_ONLY_LABEL",
    "CompetitionDemoManifestV1",
    "DemoCaseRef",
    "DemoOnlyCleanup",
    "ExpectedRelationRef",
    "ExpectedResourceRef",
    "GraphVersionRef",
    "JDInputRef",
    "CVInputRef",
    "MatchingTargetRef",
    "PositionFamilyRef",
    "PublishedPositionRef",
    "TrendWindowRef",
]
