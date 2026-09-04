"""Deterministic, disposable graph read projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.dependency_analysis import DependencyCandidate
from app.domain.traceability import MappingCandidate, RelationClaim, decide_relation_claim


ProjectionPlane = Literal["authoritative", "observed_unvalidated", "candidate"]


@dataclass(frozen=True)
class ProjectionNode:
    node_id: str
    node_type: str
    plane: ProjectionPlane
    label: str


@dataclass(frozen=True)
class ProjectionEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    edge_type: str
    plane: ProjectionPlane


@dataclass(frozen=True)
class ProjectionManifest:
    projection_version: str
    graph_version_id: int
    source_version: str
    watermark_lineage_version: str
    node_count: int
    edge_count: int


@dataclass(frozen=True)
class GraphProjection:
    nodes: tuple[ProjectionNode, ...]
    edges: tuple[ProjectionEdge, ...]
    manifest: ProjectionManifest


def _edge_id(source: str, edge_type: str, target: str) -> str:
    return f"{source}|{edge_type}|{target}"


def build_graph_projection(
    *,
    projection_version: str,
    graph_version_id: int,
    source_version: str,
    watermark_lineage_version: str,
    claims: tuple[RelationClaim, ...],
    mapping_candidates: tuple[MappingCandidate, ...] = (),
    dependency_candidates: tuple[DependencyCandidate, ...] = (),
) -> GraphProjection:
    if not projection_version.strip():
        raise ValueError("projection_version cannot be empty")
    if graph_version_id <= 0:
        raise ValueError("graph_version_id must be positive")
    if not source_version.strip():
        raise ValueError("source_version cannot be empty")
    nodes: dict[str, ProjectionNode] = {}
    edges: dict[str, ProjectionEdge] = {}

    def add_node(node: ProjectionNode) -> None:
        existing = nodes.get(node.node_id)
        if existing is not None and existing != node:
            raise ValueError(f"projection node identity collision: {node.node_id}")
        nodes[node.node_id] = node

    def add_edge(edge: ProjectionEdge) -> None:
        existing = edges.get(edge.edge_id)
        if existing is not None and existing != edge:
            raise ValueError(f"projection edge identity collision: {edge.edge_id}")
        edges[edge.edge_id] = edge

    for claim in claims:
        decision = decide_relation_claim(claim)
        if not decision.accepted:
            assert decision.rejection is not None
            raise ValueError(decision.rejection.error_code or decision.rejection.message)
        if claim.claim_kind == "inferred_candidate":
            raise ValueError("inferred claims must use candidate projection inputs")
        if claim.graph_version_id != graph_version_id:
            raise ValueError("claim graph version does not match projection graph version")
        position_node = f"position:{claim.subject_id}"
        claim_node = f"claim:{claim.claim_id}"
        skill_node = f"skill:{claim.object_id}"
        claim_plane: ProjectionPlane = (
            "authoritative"
            if claim.source_kind == "published_fact"
            and claim.validation_lineage_lineage_version is not None
            else "observed_unvalidated"
        )
        add_node(ProjectionNode(position_node, "position", "authoritative", claim.subject_id))
        add_node(ProjectionNode(claim_node, "relation_claim", claim_plane, claim.claim_id))
        add_node(ProjectionNode(skill_node, "skill", "authoritative", claim.object_id))
        for source, edge_type, target in (
            (position_node, "HAS_CLAIM", claim_node),
            (claim_node, claim.predicate, skill_node),
        ):
            identifier = _edge_id(source, edge_type, target)
            add_edge(
                ProjectionEdge(identifier, source, target, edge_type, claim_plane)
            )
        for evidence in claim.evidence:
            evidence_node = f"evidence:{evidence.evidence_id}"
            add_node(
                ProjectionNode(
                    evidence_node, "exact_evidence", claim_plane, evidence.source_id
                )
            )
            identifier = _edge_id(claim_node, "SUPPORTED_BY", evidence_node)
            add_edge(
                ProjectionEdge(
                    identifier,
                    claim_node,
                    evidence_node,
                    "SUPPORTED_BY",
                    claim_plane,
                )
            )

    for candidate in mapping_candidates:
        candidate_node = f"mapping-candidate:{candidate.candidate_id}"
        skill_node = f"skill:{candidate.proposed_skill_id}"
        add_node(
            ProjectionNode(
                candidate_node, "mapping_candidate", "candidate", candidate.source_expression
            )
        )
        add_node(
            ProjectionNode(skill_node, "skill", "authoritative", candidate.proposed_skill_id)
        )
        identifier = _edge_id(candidate_node, "PROPOSES_MAPPING", skill_node)
        add_edge(
            ProjectionEdge(
                identifier,
                candidate_node,
                skill_node,
                "PROPOSES_MAPPING",
                "candidate",
            )
        )

    for candidate in dependency_candidates:
        if candidate.claim_kind not in {"inferred_candidate", "reviewed"}:
            raise ValueError("dependency projection claim_kind is invalid")
        prerequisite_node = f"skill:{candidate.prerequisite_skill_id}"
        advanced_node = f"skill:{candidate.advanced_skill_id}"
        add_node(
            ProjectionNode(
                prerequisite_node, "skill", "authoritative", candidate.prerequisite_skill_id
            )
        )
        add_node(
            ProjectionNode(
                advanced_node, "skill", "authoritative", candidate.advanced_skill_id
            )
        )
        edge_type = (
            "REQUIRES" if candidate.claim_kind == "reviewed"
            else "STATISTICALLY_SUPPORTS"
        )
        plane: ProjectionPlane = (
            "authoritative" if candidate.claim_kind == "reviewed" else "candidate"
        )
        identifier = _edge_id(prerequisite_node, edge_type, advanced_node)
        add_edge(
            ProjectionEdge(
                identifier,
                prerequisite_node,
                advanced_node,
                edge_type,
                plane,
            )
        )

    ordered_nodes = tuple(sorted(nodes.values(), key=lambda item: item.node_id))
    ordered_edges = tuple(sorted(edges.values(), key=lambda item: item.edge_id))
    manifest = ProjectionManifest(
        projection_version=projection_version,
        graph_version_id=graph_version_id,
        source_version=source_version,
        watermark_lineage_version=watermark_lineage_version,
        node_count=len(ordered_nodes),
        edge_count=len(ordered_edges),
    )
    return GraphProjection(ordered_nodes, ordered_edges, manifest)


def verify_graph_projection(projection: GraphProjection) -> bool:
    return (
        projection.manifest.node_count == len(projection.nodes)
        and projection.manifest.edge_count == len(projection.edges)
    )
