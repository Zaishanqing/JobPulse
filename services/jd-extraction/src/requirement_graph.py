"""Deterministic Requirement Graph builder on top of existing flat facts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from .models import (
    CertificateRequirement,
    EducationRequirement,
    Evidence,
    ExperienceRequirement,
    JDExtractionResult,
    RequirementGroupChild,
    RequirementGraph,
    RequirementGroup,
    SkillRequirement,
    SoftSkillRequirement,
    ToolRequirement,
)


GRAPH_VERSION = "requirement-graph.v1"
SUPPORTED_GROUP_TYPES = frozenset({"must", "should", "and", "or", "one_of", "min_count"})
_ONE_OF_MARKERS = ("至少一个", "任一", "其中一个", "之一", "只能", "只允许", "任选其一")
_OR_CONNECTORS = ("或者", "或")
_AND_CONNECTORS = ("以及", "并且", "同时", "和", "与", "及", "并", "且")
_CLAUSE_BOUNDARIES = "，,；;。！？!?、"
_SHORT_SKILL_MAX_LENGTH = 4
_MIN_COUNT_PATTERNS = (
    re.compile(r"(?:至少|不少于|最少)(?:满足|符合)?(?:以下|下列)?\s*(\d+)\s*[项条]"),
    re.compile(r"(?:以下|下列)\s*(\d+)\s*[项条].*?(?:至少|不少于|最少)\s*(\d+)"),
)


@dataclass(frozen=True)
class _Leaf:
    requirement_id: str
    aspect: str | None
    modality: str
    quote: str
    source_id: str


def _normalized(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\s，。！？；：、；,.;!?()（）\[\]【】\-—]+", "", text)


def _identity_matches(identity: str, text: str) -> bool:
    normalized_identity = _normalized(identity)
    normalized_text = _normalized(text)
    if not normalized_identity or not normalized_text:
        return False
    if normalized_identity == normalized_text:
        return True
    identity_token = unicodedata.normalize("NFKC", identity).casefold().strip()
    if (
        len(identity_token) <= _SHORT_SKILL_MAX_LENGTH
        and re.fullmatch(r"[a-z0-9][a-z0-9+#.]*", identity_token)
    ):
        token_text = unicodedata.normalize("NFKC", text).casefold()
        return re.search(
            rf"(?<![\w+#.]){re.escape(identity_token)}(?![\w+#.])",
            token_text,
        ) is not None
    return normalized_identity in normalized_text


def _split(
    value: str,
    connectors: tuple[str, ...],
    leaves: Iterable[_Leaf] = (),
) -> list[str]:
    multi = [re.escape(item) for item in connectors if len(item) > 1]
    single = [re.escape(item) for item in connectors if len(item) == 1]
    explicit: list[str] = multi
    if single:
        alternatives = "|".join(single)
        explicit.extend(
            (
                rf"[{re.escape(_CLAUSE_BOUNDARIES)}]\s*(?:{alternatives})\s*",
                rf"\s+(?:{alternatives})\s*",
                rf"(?:{alternatives})\s+",
            )
        )
    parts = [
        part.strip()
        for part in re.split("|".join(explicit), value)
        if part.strip()
    ]
    if len(parts) > 1 or not single:
        return parts

    identities = [
        ((leaf.requirement_id, leaf.aspect), _normalized(leaf.aspect))
        for leaf in leaves
        if _normalized(leaf.aspect)
    ]
    for connector in (item for item in connectors if len(item) == 1):
        for match in re.finditer(re.escape(connector), value):
            left = _normalized(value[:match.start()])
            right = _normalized(value[match.end():])
            left_ids = {identity for identity, term in identities if term in left}
            right_ids = {identity for identity, term in identities if term in right}
            if left_ids and right_ids and left_ids != right_ids:
                return [
                    part.strip()
                    for part in (value[:match.start()], value[match.end():])
                    if part.strip()
                ]
    return [value.strip()] if value.strip() else []


def _min_count(text: str) -> int | None:
    for pattern in _MIN_COUNT_PATTERNS:
        match = pattern.search(text)
        if match:
            groups = [group for group in match.groups() if group is not None]
            return int(groups[-1])
    return None


def _aspects(requirement: Any) -> list[str | None]:
    quote = str(requirement.evidence.quote)
    if isinstance(requirement, SkillRequirement):
        if len(requirement.items) > 1:
            return [item.name for item in requirement.items]
        if "或" in quote:
            cleaned_quote = quote
            for marker in _ONE_OF_MARKERS:
                cleaned_quote = cleaned_quote.replace(marker, "")
            parts = [
                part.strip(" ：:，,。；;（）()")
                for part in _split(cleaned_quote, _OR_CONNECTORS)
                if part.strip()
            ]
            if len(parts) >= 2:
                return parts
        return [requirement.items[0].name] if requirement.items else [None]
    if isinstance(requirement, ExperienceRequirement):
        if requirement.domain:
            if "或" in requirement.domain:
                return [
                    part.strip()
                    for part in _split(requirement.domain, _OR_CONNECTORS)
                    if part.strip()
                ]
            return [requirement.domain]
        return [None]
    if isinstance(requirement, ToolRequirement):
        return list(requirement.tools) if requirement.tools else [None]
    if isinstance(requirement, CertificateRequirement):
        return list(requirement.certificates) if requirement.certificates else [None]
    if isinstance(requirement, SoftSkillRequirement):
        return list(requirement.skills) if requirement.skills else [None]
    if isinstance(requirement, EducationRequirement):
        return [None]
    value = getattr(requirement, "value", None)
    if isinstance(value, str) and value.strip():
        if "或" in value:
            return [
                part.strip()
                for part in _split(value, _OR_CONNECTORS)
                if part.strip()
            ]
        return [value.strip()]
    if "或" in quote:
        cleaned_quote = quote
        for marker in _ONE_OF_MARKERS:
            cleaned_quote = cleaned_quote.replace(marker, "")
        parts = [
            part.strip(" ：:，,。；;（）()")
            for part in _split(cleaned_quote, _OR_CONNECTORS)
            if part.strip()
        ]
        if len(parts) >= 2:
            return parts
    return [None]


def _leaves(result: JDExtractionResult) -> list[_Leaf]:
    leaves: list[_Leaf] = []
    for requirement in result.requirements:
        for aspect in _aspects(requirement):
            leaves.append(
                _Leaf(
                    requirement_id=requirement.requirement_id,
                    aspect=aspect,
                    modality=requirement.modality,
                    quote=str(requirement.evidence.quote),
                    source_id=str(requirement.evidence.source_id),
                )
            )
    return sorted(leaves, key=lambda item: (item.requirement_id, item.aspect or ""))


def _block_evidence(block: dict[str, Any]) -> Evidence:
    return Evidence(
        source_id=str(block["source_id"]),
        quote=str(block["text"]),
        start=int(block.get("start", 0)),
        end=int(block.get("end", len(str(block.get("text", ""))))),
        alignment="exact",
    )


def _priority(leaves: Iterable[_Leaf]) -> str:
    values = list(leaves)
    if any(item.modality == "required" for item in values):
        return "required"
    if values and all(item.modality in {"preferred", "bonus"} for item in values):
        return "preferred"
    return "unknown"


def _child(leaf: _Leaf) -> RequirementGroupChild:
    return RequirementGroupChild(
        node_type="requirement_ref",
        ref_id=leaf.requirement_id,
        aspect=leaf.aspect,
    )


def _leaf_matches(leaf: _Leaf, part: str) -> bool:
    if leaf.aspect:
        return _identity_matches(leaf.aspect, part)
    return _identity_matches(leaf.quote, part)


class _GraphBuilder:
    def __init__(self) -> None:
        self.groups: list[RequirementGroup] = []
        self.counter = 0

    def _group_id(self, source_id: str) -> str:
        self.counter += 1
        return f"grp-{source_id}-{self.counter:03d}"

    def _add_group(
        self,
        *,
        source_id: str,
        group_type: str,
        priority: str,
        children: list[RequirementGroupChild],
        evidence: Evidence,
        min_count: int | None = None,
        note: str | None = None,
    ) -> RequirementGroupChild:
        group = RequirementGroup(
            requirement_group_id=self._group_id(source_id),
            group_type=group_type,
            priority=priority,
            children=children,
            min_count=min_count,
            evidence=evidence,
            confidence=0.9,
            note=note,
        )
        self.groups.append(group)
        return RequirementGroupChild(
            node_type="group_ref",
            ref_id=group.requirement_group_id,
        )

    def _part_node(
        self,
        part: str,
        leaves: list[_Leaf],
        evidence: Evidence,
        source_id: str,
    ) -> RequirementGroupChild | None:
        if not leaves:
            return None
        if len(leaves) == 1:
            return _child(leaves[0])
        and_parts = _split(part, _AND_CONNECTORS, leaves)
        if len(and_parts) > 1:
            assigned: list[_Leaf] = []
            used: set[tuple[str, str | None]] = set()
            for subpart in and_parts:
                matched = [
                    leaf
                    for leaf in leaves
                    if _leaf_matches(leaf, subpart)
                    and (leaf.requirement_id, leaf.aspect) not in used
                ]
                assigned.extend(matched)
                used.update((leaf.requirement_id, leaf.aspect) for leaf in matched)
            children = [_child(leaf) for leaf in assigned]
            assigned_ids = {(leaf.requirement_id, leaf.aspect) for leaf in assigned}
            children.extend(
                _child(leaf)
                for leaf in leaves
                if (leaf.requirement_id, leaf.aspect) not in assigned_ids
            )
            if not children:
                children = [_child(leaf) for leaf in leaves]
            if len(children) == 1:
                return children[0]
        else:
            children = [_child(leaf) for leaf in leaves]
        return self._add_group(
            source_id=source_id,
            group_type="and",
            priority=_priority(leaves),
            children=children,
            evidence=evidence,
            note="nested_and_group",
        )

    def build_block(
        self, block: dict[str, Any], leaves: list[_Leaf]
    ) -> list[RequirementGroupChild]:
        if not leaves:
            return []
        text = str(block["text"])
        source_id = str(block["source_id"])
        evidence = _block_evidence(block)
        min_count = _min_count(text)
        if min_count is not None:
            top = self._add_group(
                source_id=source_id,
                group_type="min_count",
                priority=_priority(leaves),
                children=[_child(leaf) for leaf in leaves],
                evidence=evidence,
                min_count=min_count,
                note="min_count_rule",
            )
            return [top]

        or_parts = _split(text, _OR_CONNECTORS, leaves)
        if len(or_parts) > 1:
            children: list[RequirementGroupChild] = []
            used: set[tuple[str, str | None]] = set()
            for part in or_parts:
                matched = [
                    leaf
                    for leaf in leaves
                    if _leaf_matches(leaf, part)
                    and (leaf.requirement_id, leaf.aspect) not in used
                ]
                node = self._part_node(part, matched, evidence, source_id)
                if node is not None:
                    children.append(node)
                used.update((leaf.requirement_id, leaf.aspect) for leaf in matched)
            unmatched = [
                leaf
                for leaf in leaves
                if (leaf.requirement_id, leaf.aspect) not in used
            ]
            children.extend(_child(leaf) for leaf in unmatched)
            if not children:
                children = [_child(leaf) for leaf in leaves]
            if len(children) < 2:
                modality = leaves[0].modality
                if modality == "required":
                    group_type = "must"
                    priority = "required"
                elif modality in {"preferred", "bonus"}:
                    group_type = "should"
                    priority = modality
                else:
                    group_type = "must"
                    priority = "unknown"
                return [
                    self._add_group(
                        source_id=source_id,
                        group_type=group_type,
                        priority=priority,
                        children=children,
                        evidence=evidence,
                        note="or_rule_single_child",
                    )
                ]
            group_type = "one_of" if any(marker in text for marker in _ONE_OF_MARKERS) else "or"
            top = self._add_group(
                source_id=source_id,
                group_type=group_type,
                priority=_priority(leaves),
                children=children,
                evidence=evidence,
                note="or_rule",
            )
            return [top]

        and_parts = _split(text, _AND_CONNECTORS, leaves)
        if len(and_parts) > 1 and len(leaves) > 1:
            top = self._add_group(
                source_id=source_id,
                group_type="and",
                priority=_priority(leaves),
                children=[_child(leaf) for leaf in leaves],
                evidence=evidence,
                note="and_rule",
            )
            return [top]

        if len(leaves) > 1:
            group_type = "and"
            priority = _priority(leaves)
            note = "and_rule"
        else:
            modality = leaves[0].modality
            if modality == "required":
                group_type = "must"
                priority = "required"
            elif modality in {"preferred", "bonus"}:
                group_type = "should"
                priority = modality
            else:
                group_type = "must"
                priority = "unknown"
            note = "simple_rule"
        top = self._add_group(
            source_id=source_id,
            group_type=group_type,
            priority=priority,
            children=[_child(leaf) for leaf in leaves],
            evidence=evidence,
            note=note,
        )
        return [top]


def validate_requirement_graph(
    graph: RequirementGraph,
    requirement_ids: set[str],
    source_blocks: list[dict[str, Any]] | None = None,
) -> list[str]:
    errors: list[str] = []
    group_ids = [group.requirement_group_id for group in graph.groups]
    if len(group_ids) != len(set(group_ids)):
        errors.append("requirement_group_id must be unique")
    group_id_set = set(group_ids)
    blocks = {str(block.get("source_id")): str(block.get("text", "")) for block in (source_blocks or [])}
    for group in graph.groups:
        group_type = group.group_type
        if group_type not in SUPPORTED_GROUP_TYPES:
            errors.append(f"unsupported group_type: {group_type}")
        if group_type in {"and", "or", "one_of"} and len(group.children) < 2:
            errors.append(f"{group.requirement_group_id} {group_type} requires at least two children")
        if group_type == "min_count":
            if group.min_count is None or group.min_count < 1:
                errors.append(f"{group.requirement_group_id} min_count must be positive")
            elif group.min_count > len(group.children):
                errors.append(f"{group.requirement_group_id} min_count exceeds child count")
        if group_type in {"must", "should"} and not group.children:
            errors.append(f"{group.requirement_group_id} {group_type} must not be empty")
        if group_type == "must" and group.priority != "required":
            errors.append(f"{group.requirement_group_id} must group must use required priority")
        if group_type == "should" and group.priority not in {"preferred", "bonus", "unknown"}:
            errors.append(f"{group.requirement_group_id} should group has invalid priority")
        for child in group.children:
            if child.node_type == "requirement_ref" and child.ref_id not in requirement_ids:
                errors.append(f"{group.requirement_group_id} references unknown requirement {child.ref_id}")
            if child.node_type == "group_ref" and child.ref_id not in group_id_set:
                errors.append(f"{group.requirement_group_id} references unknown group {child.ref_id}")
        if source_blocks is not None:
            source_text = blocks.get(group.evidence.source_id)
            if source_text is None:
                errors.append(f"{group.requirement_group_id} evidence source_id is unknown")
            elif group.evidence.quote not in source_text:
                errors.append(f"{group.requirement_group_id} evidence quote is not in source block")
    adjacency = {
        group.requirement_group_id: [
            child.ref_id for child in group.children if child.node_type == "group_ref"
        ]
        for group in graph.groups
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(group_id: str) -> None:
        if group_id in visiting:
            errors.append(f"requirement graph cycle detected at {group_id}")
            return
        if group_id in visited:
            return
        visiting.add(group_id)
        for child_id in adjacency.get(group_id, []):
            visit(child_id)
        visiting.remove(group_id)
        visited.add(group_id)

    for group_id in adjacency:
        visit(group_id)
    return sorted(set(errors))


def build_requirement_graph(
    result: JDExtractionResult,
    source_blocks: list[dict[str, Any]],
) -> RequirementGraph:
    requirement_ids = {requirement.requirement_id for requirement in result.requirements}
    leaves = _leaves(result)
    builder = _GraphBuilder()
    used: set[tuple[str, str | None]] = set()
    blocks_by_id = {str(block.get("source_id")): block for block in source_blocks}
    for block in source_blocks:
        block_leaves = [
            leaf
            for leaf in leaves
            if leaf.source_id == str(block.get("source_id"))
            and _normalized(leaf.quote) in _normalized(str(block.get("text", "")))
        ]
        if not block_leaves:
            continue
        builder.build_block(block, block_leaves)
        used.update((leaf.requirement_id, leaf.aspect) for leaf in block_leaves)

    unmatched = [leaf for leaf in leaves if (leaf.requirement_id, leaf.aspect) not in used]
    if unmatched:
        groups_by_modality: dict[str, list[_Leaf]] = {}
        for leaf in unmatched:
            groups_by_modality.setdefault(leaf.modality, []).append(leaf)
        for modality in sorted(groups_by_modality):
            modality_leaves = groups_by_modality[modality]
            first = modality_leaves[0]
            evidence = blocks_by_id[first.source_id] if first.source_id in blocks_by_id else None
            group_evidence = _block_evidence(evidence) if evidence is not None else Evidence(
                source_id=first.source_id,
                quote=first.quote,
                alignment="unresolved",
            )
            if modality == "required":
                group_type = "must"
                priority = "required"
            elif modality in {"preferred", "bonus"}:
                group_type = "should"
                priority = modality
            else:
                group_type = "must"
                priority = "unknown"
            builder._add_group(
                source_id=first.source_id,
                group_type=group_type,
                priority=priority,
                children=[_child(leaf) for leaf in modality_leaves],
                evidence=group_evidence,
                note="fallback_group",
            )
            used.update((leaf.requirement_id, leaf.aspect) for leaf in modality_leaves)

    graph = RequirementGraph(
        graph_version=GRAPH_VERSION,
        status="unresolved",
        groups=builder.groups,
        unresolved_items=[],
    )
    errors = validate_requirement_graph(graph, requirement_ids, source_blocks)
    if errors:
        graph = RequirementGraph.model_validate(
            {
                **graph.model_dump(),
                "status": "partial" if graph.groups else "unresolved",
                "unresolved_items": errors,
            }
        )
    else:
        graph = RequirementGraph.model_validate(
            {**graph.model_dump(), "status": "complete", "unresolved_items": []}
        )
        uncovered = [
            requirement_id
            for requirement_id in sorted(requirement_ids)
            if not any(
                child.node_type == "requirement_ref" and child.ref_id == requirement_id
                for group in graph.groups
                for child in group.children
            )
        ]
        if uncovered:
            graph = RequirementGraph.model_validate(
                {
                    **graph.model_dump(),
                    "status": "partial",
                    "unresolved_items": [
                        f"requirement not covered: {item}" for item in uncovered
                    ],
                }
            )
    return graph
