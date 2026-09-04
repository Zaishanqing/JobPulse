from __future__ import annotations

import pytest
from pydantic import ValidationError

from jobgraph_contracts.evidence import Evidence
from jobgraph_contracts.extraction_v2 import JDExtractionResult
from jobgraph_contracts.requirement_graph import (
    RequirementGraph,
    RequirementGraphChild,
    RequirementGraphGroup,
)


def _evidence() -> Evidence:
    return Evidence(source_id="src-1", quote="Python")


def _child(node_type: str, ref_id: str) -> RequirementGraphChild:
    return RequirementGraphChild(node_type=node_type, ref_id=ref_id)


def _group(
    group_id: str,
    *,
    group_type: str = "and",
    children: list[RequirementGraphChild] | None = None,
    min_count: int | None = None,
) -> RequirementGraphGroup:
    resolved_children = children if children is not None else [
        _child("requirement_ref", "req-1"),
        _child("requirement_ref", "req-2"),
    ]
    return RequirementGraphGroup(
        requirement_group_id=group_id,
        group_type=group_type,
        priority="required",
        children=resolved_children,
        min_count=min_count,
        evidence=_evidence(),
    )


def _group_dict(
    group_id: str,
    *,
    group_type: str = "and",
    children: list[dict] | None = None,
    min_count: int | None = None,
) -> dict:
    return {
        "requirement_group_id": group_id,
        "group_type": group_type,
        "priority": "required",
        "children": children or [],
        "min_count": min_count,
        "evidence": _evidence().model_dump(),
    }


def test_valid_graph_accepts_nested_groups() -> None:
    graph = RequirementGraph(
        status="complete",
        groups=[
            _group(
                "root",
                children=[
                    _child("group_ref", "nested"),
                    _child("requirement_ref", "req-1"),
                ],
            ),
            _group(
                "nested",
                children=[
                    _child("requirement_ref", "req-2"),
                    _child("requirement_ref", "req-3"),
                ],
            ),
        ],
    )
    assert graph.status == "complete"


@pytest.mark.parametrize(
    "raw_groups",
    [
        [
            _group_dict("g1"),
            _group_dict("g1"),
        ],
        [
            _group_dict(
                "g1",
                children=[
                    {"node_type": "group_ref", "ref_id": "missing"},
                    {"node_type": "requirement_ref", "ref_id": "req-1"},
                ],
            )
        ],
        [
            _group_dict(
                "g1",
                children=[
                    {"node_type": "group_ref", "ref_id": "g2"},
                    {"node_type": "requirement_ref", "ref_id": "req-1"},
                ],
            ),
            _group_dict(
                "g2",
                children=[
                    {"node_type": "group_ref", "ref_id": "g1"},
                    {"node_type": "requirement_ref", "ref_id": "req-2"},
                ],
            ),
        ],
        [
            _group_dict(
                "g1",
                group_type="min_count",
                min_count=3,
                children=[{"node_type": "requirement_ref", "ref_id": "req-1"}],
            ),
        ],
        [
            _group_dict(
                "g1",
                children=[{"node_type": "requirement_ref", "ref_id": "req-1"}],
            ),
        ],
        [
            _group_dict("g1", group_type="min_count"),
        ],
    ],
)
def test_invalid_graph_structures_are_rejected(raw_groups: list[dict]) -> None:
    with pytest.raises(ValidationError):
        RequirementGraph.model_validate(
            {"status": "complete", "groups": raw_groups}
        )


def test_jd_result_rejects_unknown_requirement_reference() -> None:
    graph = RequirementGraph(
        status="complete",
        groups=[
            _group(
                "g1",
                children=[
                    _child("requirement_ref", "req-unknown"),
                    _child("requirement_ref", "req-2"),
                ],
            )
        ],
    )
    with pytest.raises(ValidationError, match="unknown requirements"):
        JDExtractionResult(
            document_id="jd-1",
            responsibilities=[],
            requirements=[],
            requirement_graph=graph,
        )


def test_graph_containers_are_immutable_tuples() -> None:
    graph = RequirementGraph(
        status="complete",
        groups=[
            _group(
                "g1",
                children=[
                    _child("requirement_ref", "req-1"),
                    _child("requirement_ref", "req-2"),
                ],
            )
        ],
    )
    assert isinstance(graph.groups, tuple)
    assert isinstance(graph.groups[0].children, tuple)
    with pytest.raises(ValidationError):
        graph.groups[0].children[0].ref_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        graph.groups[0].children = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RequirementGraphChild(
            node_type="requirement_ref",
            ref_id="",
        ),
        lambda: RequirementGraphGroup(
            requirement_group_id="",
            group_type="and",
            priority="required",
            children=[
                _child("requirement_ref", "req-1"),
                _child("requirement_ref", "req-2"),
            ],
            evidence=_evidence(),
        ),
        lambda: RequirementGraph(graph_version="", groups=[]),
    ],
)
def test_graph_ids_and_versions_must_be_non_empty(factory) -> None:
    with pytest.raises(ValidationError):
        factory()
