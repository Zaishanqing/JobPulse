"""Shared Requirement Graph schema with structural invariants baked in."""

from __future__ import annotations

from typing import Iterable, Literal

from pydantic import ConfigDict, Field, model_validator

from jobgraph_contracts.base import StrictContract
from jobgraph_contracts.evidence import Evidence


Modality = Literal["required", "preferred", "bonus", "unknown"]


class RequirementGraphChild(StrictContract):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    node_type: Literal["requirement_ref", "group_ref"]
    ref_id: str = Field(min_length=1)
    aspect: str | None = None


class RequirementGraphGroup(StrictContract):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    requirement_group_id: str = Field(min_length=1)
    group_type: Literal["must", "should", "and", "or", "one_of", "min_count"]
    priority: Modality = "unknown"
    children: tuple[RequirementGraphChild, ...] = ()
    min_count: int | None = Field(default=None, ge=1)
    evidence: Evidence
    confidence: float | None = Field(default=None, ge=0, le=1)
    note: str | None = None

    @model_validator(mode="after")
    def validate_operator(self) -> "RequirementGraphGroup":
        if self.group_type in {"and", "or", "one_of"} and len(self.children) < 2:
            raise ValueError(
                f"{self.requirement_group_id} {self.group_type} requires at least two children"
            )
        if self.group_type in {"must", "should"} and not self.children:
            raise ValueError(
                f"{self.requirement_group_id} {self.group_type} must not be empty"
            )
        if self.group_type == "min_count":
            if self.min_count is None or self.min_count > len(self.children):
                raise ValueError(
                    f"{self.requirement_group_id} min_count must be within the child count"
                )
        elif self.min_count is not None:
            raise ValueError("min_count is only valid for min_count groups")
        return self


class RequirementGraph(StrictContract):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    graph_version: str = Field(default="requirement-graph.v1", min_length=1)
    status: Literal["complete", "partial", "unresolved"] = "unresolved"
    groups: tuple[RequirementGraphGroup, ...] = ()
    unresolved_items: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_links(self) -> "RequirementGraph":
        group_ids = [group.requirement_group_id for group in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("requirement graph group ids must be unique")
        known = set(group_ids)
        edges = {
            group.requirement_group_id: tuple(
                child.ref_id for child in group.children if child.node_type == "group_ref"
            )
            for group in self.groups
        }
        if any(ref not in known for refs in edges.values() for ref in refs):
            raise ValueError("requirement graph contains an unknown group_ref")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(group_id: str) -> None:
            if group_id in visiting:
                raise ValueError("requirement graph must be acyclic")
            if group_id in visited:
                return
            visiting.add(group_id)
            for child_id in edges[group_id]:
                visit(child_id)
            visiting.remove(group_id)
            visited.add(group_id)

        for group_id in group_ids:
            visit(group_id)
        return self


def requirement_ref_ids(graph: RequirementGraph) -> set[str]:
    return {
        child.ref_id
        for group in graph.groups
        for child in group.children
        if child.node_type == "requirement_ref"
    }


def unknown_requirement_refs(
    graph: RequirementGraph,
    known_requirement_ids: Iterable[str],
) -> list[str]:
    known = set(known_requirement_ids)
    return sorted(requirement_ref_ids(graph) - known)
