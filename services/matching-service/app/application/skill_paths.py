"""Evidence-bound, bounded skill-transfer paths for learning recommendations."""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256

from app.domain.gaps import (
    PrioritizedGap,
    SkillPathDecision,
    SkillPathEdge,
    SkillTransferPath,
)
from app.domain.profiles import CVMatchProfile, Evidence
from app.domain.skill_relations import SkillRelation
from app.ports.upstream_contracts import UpstreamResponseError, UpstreamTimeoutError

_SYMMETRIC = frozenset({"equivalent"})
_WHITELIST = frozenset({"equivalent", "parent_child", "transferable"})
_EDGE_HOURS = {
    "equivalent": 2.0,
    "parent_child": 4.0,
    "transferable": 5.0,
    "related": 6.0,
}


class ControlledSkillPathPlanner:
    """Search simple one/two-hop paths without changing formal match results."""

    def __init__(
        self,
        *,
        max_hops: int = 2,
        max_cost_hours: float = 16.0,
        minimum_confidence: float = 0.7,
        hop_discount: float = 0.8,
    ) -> None:
        self._max_hops = max_hops
        self._max_cost_hours = max_cost_hours
        self._minimum_confidence = minimum_confidence
        self._hop_discount = hop_discount

    def plan(
        self,
        cv: CVMatchProfile,
        gaps: tuple[PrioritizedGap, ...],
        fetch_relations: Callable[[tuple[str, ...]], tuple[SkillRelation, ...] | None],
        *,
        graph_enabled: bool,
        expected_graph_version: str,
    ) -> tuple[SkillPathDecision, ...]:
        targets = tuple(
            (gap.requirement_id, gap.skill_id, gap.gap_type)
            for gap in gaps
            if gap.skill_id is not None
            and gap.gap_type in {"required_skill_missing", "skill_level_gap"}
        )
        if not targets:
            return ()
        source_evidence = self._verified_source_evidence(cv)
        if not graph_enabled:
            return self._unreachable(targets, "GRAPH_MODE_DISABLED", "unavailable")
        if not source_evidence:
            return self._unreachable(targets, "NO_ELIGIBLE_SOURCE_SKILL", "available")
        query_ids = tuple(sorted(set(source_evidence) | {item[1] for item in targets}))
        try:
            first = fetch_relations(query_ids)
            if first is None:
                return self._unreachable(targets, "RELATION_SOURCE_UNAVAILABLE", "unavailable")
            expanded_ids = tuple(
                sorted(
                    set(query_ids)
                    | {item.source_skill_id for item in first}
                    | {item.target_skill_id for item in first}
                )
            )
            second = fetch_relations(expanded_ids)
            relations = self._dedupe((*first, *(second or ())))
        except (UpstreamResponseError, UpstreamTimeoutError):
            return self._unreachable(targets, "RELATION_SOURCE_ERROR", "error")

        return tuple(
            self._decision(
                requirement_id,
                skill_id,
                gap_type,
                source_evidence,
                relations,
                expected_graph_version,
            )
            for requirement_id, skill_id, gap_type in targets
        )

    def _decision(
        self,
        requirement_id: str,
        target_skill_id: str,
        gap_type: str,
        source_evidence: dict[str, tuple[Evidence, ...]],
        relations: tuple[SkillRelation, ...],
        expected_graph_version: str,
    ) -> SkillPathDecision:
        if gap_type == "required_skill_missing" and target_skill_id in source_evidence:
            return self._unreachable(
                ((requirement_id, target_skill_id),),
                "TARGET_EXACT_SATISFIED",
                "available",
            )
        paths: list[SkillTransferPath] = []
        rejected: set[str] = set()
        adjacency: dict[str, list[tuple[str, SkillRelation]]] = {}
        for relation in relations:
            if relation.relation_type not in _WHITELIST:
                continue
            if relation.graph_version != expected_graph_version:
                rejected.add("GRAPH_VERSION_MISMATCH")
                continue
            if not relation.evidence_refs:
                rejected.add("RELATION_EVIDENCE_MISSING")
                continue
            if relation.confidence < self._minimum_confidence:
                rejected.add("RELATION_CONFIDENCE_BELOW_THRESHOLD")
                continue
            adjacency.setdefault(relation.source_skill_id, []).append(
                (relation.target_skill_id, relation)
            )
            if relation.relation_type in _SYMMETRIC:
                adjacency.setdefault(relation.target_skill_id, []).append(
                    (relation.source_skill_id, relation)
                )

        for source_skill_id in sorted(source_evidence):
            frontier = [(source_skill_id, (source_skill_id,), tuple())]
            while frontier:
                node, nodes, edges = frontier.pop(0)
                if len(edges) >= self._max_hops:
                    continue
                for neighbor, relation in sorted(
                    adjacency.get(node, ()), key=lambda item: (item[0], item[1].relation_id)
                ):
                    if neighbor in nodes:
                        rejected.add("PATH_CYCLE_REJECTED")
                        continue
                    next_edges = (*edges, relation)
                    next_nodes = (*nodes, neighbor)
                    versions = {item.graph_version for item in next_edges}
                    if len(versions) != 1:
                        rejected.add("GRAPH_VERSION_MISMATCH")
                        continue
                    total_cost = sum(_EDGE_HOURS[item.relation_type] for item in next_edges)
                    if total_cost > self._max_cost_hours:
                        rejected.add("PATH_COST_EXCEEDED")
                        continue
                    if neighbor == target_skill_id:
                        paths.append(
                            self._path(
                                requirement_id,
                                source_skill_id,
                                target_skill_id,
                                next_nodes,
                                next_edges,
                                total_cost,
                            )
                        )
                    elif len(next_edges) < self._max_hops:
                        frontier.append((neighbor, next_nodes, next_edges))

        unique = {item.path_id: item for item in paths}
        ranked = tuple(
            sorted(
                unique.values(),
                key=lambda item: (
                    item.hop_count,
                    item.total_cost_hours,
                    -item.effective_confidence,
                    item.path_id,
                ),
            )
        )
        return SkillPathDecision(
            target_requirement_id=requirement_id,
            target_skill_id=target_skill_id,
            status="reachable" if ranked else "unreachable",
            paths=ranked,
            reason_codes=() if ranked else tuple(sorted(rejected or {"NO_CONTROLLED_PATH"})),
            max_hops=self._max_hops,
            max_cost_hours=self._max_cost_hours,
            relation_whitelist=tuple(sorted(_WHITELIST)),
            source_status="available",
        )

    def _path(
        self,
        requirement_id: str,
        source_skill_id: str,
        target_skill_id: str,
        nodes: tuple[str, ...],
        relations: tuple[SkillRelation, ...],
        total_cost: float,
    ) -> SkillTransferPath:
        minimum_confidence = min(item.confidence for item in relations)
        effective = minimum_confidence * (self._hop_discount ** (len(relations) - 1))
        identity = "|".join((requirement_id, *nodes, *(item.relation_id for item in relations)))
        return SkillTransferPath(
            path_id=f"skill-path:{sha256(identity.encode('utf-8')).hexdigest()[:16]}",
            source_skill_id=source_skill_id,
            target_skill_id=target_skill_id,
            target_requirement_id=requirement_id,
            node_skill_ids=nodes,
            edges=tuple(
                SkillPathEdge(
                    relation_id=item.relation_id,
                    source_skill_id=nodes[index],
                    target_skill_id=nodes[index + 1],
                    relation_type=item.relation_type,
                    graph_version=item.graph_version,
                    confidence=item.confidence,
                    hop_number=index + 1,
                    edge_cost_hours=_EDGE_HOURS[item.relation_type],
                    evidence_refs=item.evidence_refs,
                    score_credit_allowed=item.relation_type
                    in {"equivalent", "transferable"},
                )
                for index, item in enumerate(relations)
            ),
            hop_count=len(relations),
            total_cost_hours=round(total_cost, 4),
            minimum_confidence=minimum_confidence,
            effective_confidence=round(effective, 6),
            outcome_status="eligible" if len(relations) == 1 else "partial",
            graph_version_id=relations[0].graph_version,
            score_credit_allowed=all(
                item.relation_type in {"equivalent", "transferable"}
                for item in relations
            ),
            suitable_for_learning=True,
        )

    @staticmethod
    def _verified_source_evidence(cv: CVMatchProfile) -> dict[str, tuple[Evidence, ...]]:
        links = {item.link_id: item for item in cv.capability_evidence_links}
        output: dict[str, tuple[Evidence, ...]] = {}
        for capability in cv.capability_profiles:
            evidence = tuple(
                evidence
                for link_id in capability.evidence_link_ids
                if (link := links.get(link_id)) is not None
                for evidence in link.evidence_refs
            )
            if (
                capability.skill_id
                and capability.resolution_status == "resolved"
                and capability.verification_status
                in {"supported", "partially_supported", "experience_only"}
                and capability.demonstrated_level != "unknown"
                and evidence
            ):
                output[capability.skill_id] = evidence
        return output

    def _unreachable(
        self,
        targets: tuple[tuple[str, str, str] | tuple[str, str], ...],
        reason: str,
        source_status: str,
    ) -> tuple[SkillPathDecision, ...]:
        return tuple(
            SkillPathDecision(
                target_requirement_id=requirement_id,
                target_skill_id=skill_id,
                status="unreachable",
                reason_codes=(reason,),
                max_hops=self._max_hops,
                max_cost_hours=self._max_cost_hours,
                relation_whitelist=tuple(sorted(_WHITELIST)),
                source_status=source_status,
            )
            for requirement_id, skill_id, *_rest in targets
        )

    @staticmethod
    def _dedupe(relations: tuple[SkillRelation, ...]) -> tuple[SkillRelation, ...]:
        by_id = {item.relation_id: item for item in relations}
        return tuple(by_id[key] for key in sorted(by_id))
