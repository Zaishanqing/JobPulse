"""Deterministic evaluation of enterprise-JD Requirement Graph operators."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.domain.evaluation import MatchEvaluation, RequirementGroupResult, SkillResult
from app.domain.profiles import (
    PositionMatchProfile,
    PositionSkillRequirement,
    RequirementGraph,
    RequirementGraphChild,
    RequirementGraphGroup,
)

_SATISFIED = frozenset({"matched", "pass"})
_PARTIAL = frozenset({"partial", "weak", "declared_only"})
_UNSATISFIED = frozenset({"missing", "fail", "not_observed"})
_UNKNOWN = frozenset({"unknown"})
_OR_SEPARATOR = re.compile(
    r"(/|或|或者|\bor\b|至少一种|至少一门|至少一个|任选|其中一种|"
    r"one[- ]?of|any[- ]?of|either)"
)
_SEGMENT_SPLIT = re.compile(r"[，。；;,]")
_SKILL_TEXT_ALIASES = (("扣子", "coze"),)
SPECIALTY_ROUTE_GRAPH_VERSION = "standard-position-specialty-routes.v2"
SPECIALTY_ROUTE_ROOT_PREFIX = "standard-route-root:"
SPECIALTY_ROUTE_GROUP_PREFIX = "standard-route:"


@dataclass(frozen=True)
class SpecialtyRouteSelection:
    """The deterministic route selected for one standard-position evaluation."""

    route_id: str
    required_requirement_ids: tuple[str, ...]
    score: float
    exact_match_count: int
    evaluable_count: int


def _contains_skill(quote: str, name: str) -> bool:
    if not name:
        return False
    normalized_quote = quote.casefold()
    normalized_name = name.casefold()
    if normalized_name in normalized_quote:
        return True
    for alias, canonical in _SKILL_TEXT_ALIASES:
        if canonical == normalized_name and alias in normalized_quote:
            return True
    # Prefix alias: canonical "Vue.js" matches a bare "Vue" token in the text.
    prefix = re.split(r"[^a-z0-9+#]+", normalized_name, maxsplit=1)[0]
    if len(prefix) >= 2 and re.search(
        r"(?<![a-z0-9])" + re.escape(prefix) + r"(?![a-z0-9])",
        normalized_quote,
    ):
        return True
    return False


def _or_group_for_segment(
    segment: str,
    skills: tuple[PositionSkillRequirement, ...],
) -> tuple[PositionSkillRequirement, ...]:
    """Skills in one clause that are alternatives around an OR separator.

    A separator only creates an alternative when both of its adjacent pieces
    mention at least one skill.  This keeps conjunctive skills in the same
    clause (for example ``熟练掌握 Python,熟悉 C++ 或 Java``) out of the group.
    """

    pieces = [piece for piece in _OR_SEPARATOR.split(segment) if piece is not None]
    joined: list[PositionSkillRequirement] = []
    for index in range(1, len(pieces), 2):
        left = pieces[index - 1]
        right = pieces[index + 1] if index + 1 < len(pieces) else ""
        left_skills = [
            item for item in skills if _contains_skill(left, item.canonical_name or "")
        ]
        right_skills = [
            item for item in skills if _contains_skill(right, item.canonical_name or "")
        ]
        if not left_skills or not right_skills:
            continue
        for item in (*left_skills, *right_skills):
            if item not in joined:
                joined.append(item)
    return tuple(joined)


def build_requirement_graph_from_jd(
    position: PositionMatchProfile,
) -> RequirementGraph | None:
    """Derive OR/one-of requirement groups from JD evidence text.

    Atomic requirements remain in the profile; the derived graph only marks
    alternatives so the scorer, gap analysis and What-if do not treat every
    child of an OR clause as simultaneously mandatory.
    """

    all_requirements = (*position.required_skills, *position.preferred_skills)
    required_ids = {item.requirement_id for item in position.required_skills}
    preferred_ids = {item.requirement_id for item in position.preferred_skills}
    by_quote: dict[str, list[PositionSkillRequirement]] = {}
    for requirement in all_requirements:
        for evidence in requirement.evidence_refs:
            by_quote.setdefault(evidence.quote, []).append(requirement)

    groups: list[RequirementGraphGroup] = []
    for quote, requirements in sorted(by_quote.items()):
        unique = tuple(
            dict.fromkeys(
                item for item in requirements if item.requirement_id is not None
            )
        )
        if len(unique) < 2:
            continue
        segments = [part for part in _SEGMENT_SPLIT.split(quote) if part.strip()]
        if not segments:
            segments = [quote]
        for segment_index, segment in enumerate(segments, 1):
            if not _OR_SEPARATOR.search(segment):
                continue
            members = _or_group_for_segment(segment, unique)
            if len(members) < 2:
                continue
            identity = (
                f"{quote}|{segment_index}|"
                + "|".join(item.requirement_id for item in members)
            )
            group_id = (
                "derived-or:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
            )
            member_ids = {item.requirement_id for item in members}
            priorities = set()
            if member_ids.intersection(required_ids):
                priorities.add("required")
            if member_ids.intersection(preferred_ids):
                priorities.add("preferred")
            priority = (
                "required"
                if priorities == {"required"}
                else "preferred"
                if priorities == {"preferred"}
                else "unknown"
            )
            evidence = next(
                (
                    ref
                    for item in members
                    for ref in item.evidence_refs
                    if ref.quote == quote
                ),
                members[0].evidence_refs[0] if members[0].evidence_refs else None,
            )
            if evidence is None:
                continue
            groups.append(
                RequirementGraphGroup(
                    requirement_group_id=group_id,
                    group_type="or",
                    priority=priority,
                    children=tuple(
                        RequirementGraphChild(
                            node_type="requirement_ref",
                            ref_id=item.requirement_id or "",
                            aspect=item.canonical_name,
                        )
                        for item in members
                    ),
                    evidence=evidence,
                    confidence=0.8,
                    note="derived from JD alternative-language evidence",
                )
            )
    if not groups:
        return None
    return RequirementGraph(
        graph_version="derived-requirement-graph.v1",
        status="partial",
        groups=tuple(groups),
        unresolved_items=(),
    )


def _key(value: str | None) -> str:
    return "".join(
        character
        for character in (value or "").casefold()
        if character.isalnum() or character in "+#."
    )


def is_specialty_route_graph(graph: RequirementGraph | None) -> bool:
    return bool(
        graph is not None
        and graph.graph_version == SPECIALTY_ROUTE_GRAPH_VERSION
    )


def _skill_requirement_id(item: PositionSkillRequirement, importance: str) -> str:
    return item.requirement_id or f"{importance}:{item.skill_id or item.canonical_name}"


def _specialty_route_definitions(
    graph: RequirementGraph | None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return source-JD route groups and their unique atomic requirement refs."""

    if not is_specialty_route_graph(graph):
        return ()
    assert graph is not None
    groups = {group.requirement_group_id: group for group in graph.groups}
    referenced = {
        child.ref_id
        for group in graph.groups
        for child in group.children
        if child.node_type == "group_ref"
    }
    roots = tuple(
        group
        for group in graph.groups
        if group.requirement_group_id not in referenced
        and group.requirement_group_id.startswith(SPECIALTY_ROUTE_ROOT_PREFIX)
    )
    if len(roots) != 1:
        return ()
    root = roots[0]

    def atomic_refs(group_id: str, visiting: frozenset[str]) -> tuple[str, ...]:
        if group_id in visiting:
            return ()
        group = groups.get(group_id)
        if group is None:
            return ()
        refs: list[str] = []
        next_visiting = visiting | {group_id}
        for child in group.children:
            if child.node_type == "requirement_ref":
                if child.ref_id not in refs:
                    refs.append(child.ref_id)
            elif child.node_type == "group_ref":
                for ref_id in atomic_refs(child.ref_id, next_visiting):
                    if ref_id not in refs:
                        refs.append(ref_id)
        return tuple(refs)

    definitions: list[tuple[str, tuple[str, ...]]] = []
    for child in root.children:
        if child.node_type != "group_ref" or not child.ref_id.startswith(
            SPECIALTY_ROUTE_GROUP_PREFIX
        ):
            continue
        refs = atomic_refs(child.ref_id, frozenset())
        if refs:
            definitions.append((child.ref_id, refs))
    return tuple(definitions)


def select_specialty_route(
    position: PositionMatchProfile,
    skill_results: tuple[SkillResult, ...],
) -> SpecialtyRouteSelection | None:
    """Choose one source-JD route using the existing graph group evaluator."""

    definitions = _specialty_route_definitions(position.requirement_graph)
    if not definitions:
        return None
    route_evaluation = MatchEvaluation(
        evaluation_id="specialty-route-selection",
        algorithm_version="specialty-route-selection.v1",
        evaluation_status="completed",
        skill_results=skill_results,
    )
    route_results = {
        item.group_id: item
        for item in evaluate_requirement_graph(position, route_evaluation)
    }
    result_by_id: dict[str, SkillResult] = {}
    for item in skill_results:
        result_by_id.setdefault(item.requirement_id, item)

    candidates = []
    for route_id, requirement_ids in definitions:
        route_result = route_results[route_id]
        route_score = (
            route_result.score * route_result.evaluable_count / len(requirement_ids)
            if route_result.score is not None and requirement_ids
            else 0.0
        )
        exact_match_count = 0
        for requirement_id in requirement_ids:
            result = result_by_id.get(requirement_id)
            if (
                result is not None
                and result.match_status == "matched"
                and result.match_type == "exact"
            ):
                exact_match_count += 1
        candidates.append(
            SpecialtyRouteSelection(
                route_id=route_id,
                required_requirement_ids=tuple(dict.fromkeys(requirement_ids)),
                score=route_score,
                exact_match_count=exact_match_count,
                evaluable_count=route_result.evaluable_count,
            )
        )
    return min(
        candidates,
        key=lambda item: (
            -item.score,
            -item.exact_match_count,
            -item.evaluable_count,
            item.route_id,
        ),
    )


def apply_effective_required_set(
    position: PositionMatchProfile,
    selection: SpecialtyRouteSelection | None,
) -> PositionMatchProfile:
    """Promote the selected route and demote other route branches to preferred."""

    if selection is None:
        return position
    selected_ids = set(selection.required_requirement_ids)
    required: list[PositionSkillRequirement] = []
    preferred: list[PositionSkillRequirement] = []
    for importance, items in (
        ("required", position.required_skills),
        ("bonus", position.preferred_skills),
    ):
        for item in items:
            requirement_id = _skill_requirement_id(item, importance)
            if requirement_id in selected_ids:
                required.append(item)
            else:
                preferred.append(item)
    return position.model_copy(
        update={
            "required_skills": tuple(required),
            "preferred_skills": tuple(preferred),
        }
    )


def _atomic_status(
    child: RequirementGraphChild,
    evaluation: MatchEvaluation,
) -> tuple[str, float | None, float, str, tuple[str, ...]]:
    """Return status plus the flat dimension that this graph leaf replaces."""
    candidates: list[tuple[str, str, float, str]] = []
    for item in evaluation.skill_results:
        if item.requirement_id == child.ref_id or (
            child.aspect and _key(item.skill_name) == _key(child.aspect)
        ):
            dimension = (
                "required_skills"
                if item.importance_level == "required"
                else "bonus_transferable"
            )
            candidates.append(
                (item.requirement_id, item.match_status, item.confidence, dimension)
            )
    for item in evaluation.hard_constraint_results:
        if item.requirement_id == child.ref_id:
            candidates.append(
                (item.requirement_id, item.status, item.confidence, "hard_conditions")
            )
    for dimension, results in (
        ("responsibilities", evaluation.responsibility_results),
        ("projects", evaluation.project_results),
        ("business_scenarios", evaluation.scenario_results),
    ):
        for item in results:
            if item.requirement_id == child.ref_id:
                candidates.append(
                    (item.requirement_id, item.match_status, item.confidence, dimension)
                )

    if not candidates:
        return "unresolved", None, 0.0, child.ref_id, ()
    result_id, status, confidence, dimension = max(
        candidates, key=lambda value: value[2]
    )
    if status in _SATISFIED:
        return "satisfied", 1.0, confidence, result_id, (dimension,)
    if status in _PARTIAL:
        return "partial", 0.5, confidence, result_id, (dimension,)
    if status in _UNSATISFIED:
        return "unsatisfied", 0.0, confidence, result_id, (dimension,)
    if status in _UNKNOWN:
        return "unknown", None, confidence, result_id, (dimension,)
    return "unresolved", None, confidence, result_id, (dimension,)


def evaluate_requirement_graph(
    position: PositionMatchProfile,
    evaluation: MatchEvaluation,
    *,
    selected_route_id: str | None = None,
) -> tuple[RequirementGroupResult, ...]:
    """Evaluate nested groups without turning composite requirements into hard gates."""
    graph = position.requirement_graph
    if graph is None or not graph.groups:
        return ()
    groups = {group.requirement_group_id: group for group in graph.groups}
    referenced = {
        child.ref_id
        for group in graph.groups
        for child in group.children
        if child.node_type == "group_ref"
    }
    specialty_route_graph = is_specialty_route_graph(graph)
    cache: dict[str, RequirementGroupResult] = {}

    def resolve(group_id: str) -> RequirementGroupResult:
        if group_id in cache:
            return cache[group_id]
        group = groups[group_id]
        children: list[tuple[str, float | None, float, str, tuple[str, ...]]] = []
        covered_ids: set[str] = set()
        covered_dimensions: set[str] = set()
        selected_child_index: int | None = None
        seen_specialty_requirement_refs: set[str] = set()
        for child in group.children:
            if (
                specialty_route_graph
                and group.requirement_group_id.startswith(SPECIALTY_ROUTE_GROUP_PREFIX)
                and child.node_type == "requirement_ref"
                and child.ref_id in seen_specialty_requirement_refs
            ):
                continue
            if (
                specialty_route_graph
                and group.requirement_group_id.startswith(SPECIALTY_ROUTE_GROUP_PREFIX)
                and child.node_type == "requirement_ref"
            ):
                seen_specialty_requirement_refs.add(child.ref_id)
            if child.node_type == "group_ref":
                nested = resolve(child.ref_id)
                child_index = len(children)
                children.append(
                    (
                        nested.status,
                        nested.score,
                        nested.confidence,
                        nested.group_id,
                        nested.covered_dimensions,
                    )
                )
                if (
                    specialty_route_graph
                    and selected_route_id is not None
                    and group.requirement_group_id.startswith(SPECIALTY_ROUTE_ROOT_PREFIX)
                    and child.ref_id == selected_route_id
                ):
                    selected_child_index = child_index
                if (
                    specialty_route_graph
                    and selected_route_id is not None
                    and group.requirement_group_id.startswith(SPECIALTY_ROUTE_ROOT_PREFIX)
                    and child.ref_id != selected_route_id
                ):
                    continue
                covered_ids.update(nested.covered_result_ids)
                covered_dimensions.update(nested.covered_dimensions)
            else:
                atomic = _atomic_status(child, evaluation)
                children.append(atomic)
                covered_ids.add(atomic[3])
                covered_dimensions.update(atomic[4])

        metric_children = (
            [children[selected_child_index]]
            if selected_child_index is not None
            else children
        )
        evaluable = [item for item in metric_children if item[1] is not None]
        scores = [float(item[1]) for item in evaluable if item[1] is not None]
        uncertain_count = len(metric_children) - len(evaluable)
        satisfied_count = sum(score >= 1.0 for score in scores)
        if group.group_type in {"or", "one_of"}:
            required_count = 1
            best = max(scores) if scores else None
            score = None if uncertain_count and (best is None or best <= 0.0) else best
        elif group.group_type == "min_count":
            required_count = int(group.min_count or 1)
            progress = sum(scores)
            score = (
                None
                if uncertain_count and progress <= 0.0
                else min(1.0, progress / required_count) if scores else None
            )
        else:
            required_count = (
                len(metric_children)
                if specialty_route_graph
                and group.requirement_group_id.startswith(SPECIALTY_ROUTE_GROUP_PREFIX)
                else len(group.children)
            )
            score = sum(scores) / len(scores) if scores else None

        if score is None:
            status = "unknown" if any(item[0] == "unknown" for item in children) else "unresolved"
        elif score >= 1.0 and (
            not uncertain_count or group.group_type in {"or", "one_of", "min_count"}
        ):
            status = "satisfied"
        elif score <= 0.0:
            status = "unsatisfied"
        else:
            status = "partial"
        confidences = [item[2] for item in evaluable]
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        if metric_children:
            confidence *= len(evaluable) / len(metric_children)
        result = RequirementGroupResult(
            group_id=group.requirement_group_id,
            group_type=group.group_type,
            priority=group.priority,
            status=status,
            required_count=required_count,
            satisfied_count=satisfied_count,
            evaluable_count=len(evaluable),
            child_result_ids=tuple(item[3] for item in children),
            covered_result_ids=tuple(sorted(covered_ids)),
            covered_dimensions=tuple(sorted(covered_dimensions)),
            is_root=group.requirement_group_id not in referenced,
            score=score,
            reason_code=f"REQUIREMENT_GROUP_{status.upper()}",
            confidence=round(confidence, 6),
            position_evidence=(group.evidence,),
        )
        cache[group_id] = result
        return result

    for group in graph.groups:
        resolve(group.requirement_group_id)
    return tuple(cache[group.requirement_group_id] for group in graph.groups)
