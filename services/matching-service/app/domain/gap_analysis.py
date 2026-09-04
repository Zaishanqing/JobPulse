"""Pure deterministic gap prioritization and evidence-grounded learning-path rules."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from app.domain.evaluation import MatchEvaluation
from app.domain.gaps import (
    CounterfactualSuggestion,
    GapAnalysis,
    GapPriority,
    GapType,
    LearningStep,
    PrioritizedGap,
    ProfileReferences,
)
from app.domain.profiles import Evidence


@dataclass(frozen=True)
class GapAnalysisConfig:
    algorithm_version: str = "deterministic-gap-path.v3"
    config_version: str = "gap-analysis-config.v3"
    gap_policy_version: str = "gap-priority.v3"
    required_factor_weight: float = 0.25
    severity_weight: float = 0.25
    score_impact_weight: float = 0.20
    requirement_weight: float = 0.15
    prerequisite_weight: float = 0.10
    evidence_weight: float = 0.05
    transferable_reduction_weight: float = 0.15
    # 低分维度提权：缺口对应的评分维度分数低于阈值时，按低分程度给该缺口
    # 追加确定性优先级权重，让学习路径优先补强当前最弱的能力维度。
    low_dimension_threshold: float = 60.0
    low_dimension_boost: float = 0.12
    critical_threshold: float = 75.0
    high_threshold: float = 55.0
    medium_threshold: float = 30.0
    hard_gate_readiness_cap: float = 0.25
    capability_levels: tuple[str, ...] = (
        "unknown",
        "basic",
        "working",
        "proficient",
        "advanced",
        "expert",
    )

    def __post_init__(self) -> None:
        positive = (
            self.required_factor_weight,
            self.severity_weight,
            self.score_impact_weight,
            self.requirement_weight,
            self.prerequisite_weight,
            self.evidence_weight,
        )
        if any(value < 0 or value > 1 for value in positive):
            raise ValueError("gap priority factors must be between 0 and 1")
        if abs(sum(positive) - 1.0) > 1e-9:
            raise ValueError("positive gap priority factors must sum to 1.0")
        if not 0 <= self.transferable_reduction_weight <= 1:
            raise ValueError("transferable reduction must be between 0 and 1")
        if not 0 <= self.low_dimension_threshold <= 100:
            raise ValueError("low dimension threshold must be within 0..100")
        if not 0 <= self.low_dimension_boost <= 1:
            raise ValueError("low dimension boost must be within 0..1")
        if not (
            0
            <= self.medium_threshold
            <= self.high_threshold
            <= self.critical_threshold
            <= 100
        ):
            raise ValueError("gap priority thresholds must be ordered within 0..100")
        if not 0 <= self.hard_gate_readiness_cap <= 1:
            raise ValueError("hard gate readiness cap must be between 0 and 1")
        if len(set(self.capability_levels)) != len(self.capability_levels):
            raise ValueError("capability level order must not contain duplicates")


def gap_policy_hash(config: GapAnalysisConfig) -> str:
    """Deterministic fingerprint of every factor that shapes priority ordering.

    Covers the six positive weights, the transferable reduction, the three
    priority thresholds and the capability-level order. Any change here changes
    the hash, so historical reports can explain why one gap ranked above
    another under the policy in effect at generation time.
    """

    canonical = (
        "|".join(
            (
                config.gap_policy_version,
                *(f"{value:.10g}" for value in (
                    config.required_factor_weight,
                    config.severity_weight,
                    config.score_impact_weight,
                    config.requirement_weight,
                    config.prerequisite_weight,
                    config.evidence_weight,
                    config.transferable_reduction_weight,
                    config.low_dimension_threshold,
                    config.low_dimension_boost,
                    config.critical_threshold,
                    config.high_threshold,
                    config.medium_threshold,
                )),
                ",".join(config.capability_levels),
            )
        )
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _GapSeed:
    gap_type: GapType
    requirement_id: str
    skill_id: str | None
    current_level: str | None
    target_level: str | None
    status: str
    reason_codes: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    position_evidence_present: bool
    candidate_evidence_present: bool
    source_match_type: str | None
    transferable_skill_ids: tuple[str, ...]
    prerequisite_skill_ids: tuple[str, ...]
    transferability: float
    mandatory: float
    severity: float
    requirement_importance: float
    current_ownership: str | None = None
    target_ownership: str | None = None
    score_effect_status: str = "modeled"


_OWNERSHIP_ORDER = {
    "unknown": 0,
    "declared": 0,
    "used": 1,
    "participated": 1,
    "implemented": 2,
    "owned": 3,
    "designed": 4,
    "led": 5,
}


def _ownership_below(current: str | None, target: str | None) -> bool:
    return bool(
        current in _OWNERSHIP_ORDER
        and target in _OWNERSHIP_ORDER
        and _OWNERSHIP_ORDER[current] < _OWNERSHIP_ORDER[target]
    )


def _dedupe_evidence(*groups: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    unique = {
        (item.source_id, item.start, item.end, item.quote): item
        for group in groups
        for item in group
    }
    return tuple(unique[key] for key in sorted(unique, key=str))


def _skill_severity(item, config: GapAnalysisConfig) -> float:
    if item.match_status == "missing":
        return 1.0
    if item.match_status == "matched" and item.match_type == "semantic_text":
        return 0.55
    if item.match_status == "matched" and item.proficiency_satisfied is False:
        if (
            item.required_level in config.capability_levels
            and item.candidate_demonstrated_level in config.capability_levels
        ):
            target = config.capability_levels.index(item.required_level)
            current = config.capability_levels.index(item.candidate_demonstrated_level)
            return max(0.35, min(1.0, (target - current) / max(target, 1)))
        return 0.55
    if item.match_status == "weak":
        if (
            item.required_level in config.capability_levels
            and item.candidate_demonstrated_level in config.capability_levels
        ):
            target = config.capability_levels.index(item.required_level)
            current = config.capability_levels.index(item.candidate_demonstrated_level)
            return max(0.35, min(1.0, (target - current) / max(target, 1)))
        return 0.55
    if item.match_status == "partial":
        return max(0.35, 1.0 - item.transferability_score)
    if item.match_status in {"declared_only", "unknown"}:
        return 0.40
    return 0.50


def _skill_seeds(evaluation: MatchEvaluation, config: GapAnalysisConfig) -> list[_GapSeed]:
    seeds = []
    for item in evaluation.skill_results:
        ownership_gap = _ownership_below(
            item.candidate_ownership, item.required_ownership
        )
        is_bonus = item.importance_level != "required"
        if (
            item.match_status == "matched"
            and not ownership_gap
            and item.proficiency_satisfied is not False
            and item.evidence_sufficient
        ):
            continue
        if is_bonus:
            # 可加分能力：口径从“可迁移”调整为岗位加分技能，缺失时按必备技能
            # 一样进入学习缺口，但优先级更低的唯一缺口类型。
            if item.match_status in {"matched", "declared_only"}:
                continue
            gap_type: GapType = "bonus_skill_missing"
            category_reason = "BONUS_SKILL_GAP"
        elif ownership_gap and item.match_status != "declared_only":
            gap_type: GapType = "ownership_gap"
            category_reason = "SKILL_OWNERSHIP_GAP"
        elif item.match_status == "matched" and item.proficiency_satisfied is False:
            gap_type: GapType = "skill_level_gap"
            category_reason = "SKILL_LEVEL_GAP"
        elif item.match_status == "matched" and item.evidence_sufficient is False:
            gap_type = "evidence_gap"
            category_reason = "SKILL_EVIDENCE_GAP"
        elif item.match_status == "matched":
            gap_type = "skill_level_gap"
            category_reason = "SKILL_LEVEL_UNVERIFIED"
        elif item.match_status == "weak":
            gap_type: GapType = "skill_level_gap"
            category_reason = "SKILL_LEVEL_GAP"
        elif item.match_status == "declared_only":
            gap_type = "usage_evidence_gap"
            category_reason = "SKILL_USAGE_EVIDENCE_GAP"
        elif item.match_status == "unknown":
            gap_type = "evidence_gap"
            category_reason = "SKILL_EVIDENCE_GAP"
        elif item.match_status == "unresolved":
            gap_type = "unresolved_gap"
            category_reason = "SKILL_RESOLUTION_REQUIRED"
        else:
            gap_type = "required_skill_missing"
            category_reason = "REQUIRED_SKILL_GAP"
        transferable_ids = (
            (item.related_candidate_skill_id,)
            if item.related_candidate_skill_id is not None
            else ()
        )
        seeds.append(
            _GapSeed(
                gap_type=gap_type,
                requirement_id=item.requirement_id,
                skill_id=item.skill_id,
                current_level=(
                    item.candidate_demonstrated_level or item.candidate_declared_level
                ),
                target_level=item.required_level,
                status=item.match_status,
                reason_codes=(item.reason_code, category_reason),
                evidence=_dedupe_evidence(
                    item.position_evidence,
                    item.candidate_evidence,
                    item.relation_evidence,
                ),
                position_evidence_present=bool(item.position_evidence),
                candidate_evidence_present=bool(item.candidate_evidence),
                source_match_type=item.match_type,
        transferable_skill_ids=transferable_ids,
        prerequisite_skill_ids=(
            tuple(item.prerequisite_skill_ids or ())
            if item.match_type == "prerequisite"
            or item.relation_type == "prerequisite"
            else tuple(item.prerequisite_skill_ids or ())
        ),
        transferability=(
                    0.25
                    if item.match_type == "prerequisite"
                    else item.transferability_score
                    if item.match_type in {"parent_child", "related", "transferable"}
                    else 0.0
                ),
                mandatory=0.5 if is_bonus else 1.0,
                severity=_skill_severity(item, config),
                requirement_importance=item.requirement_weight,
                current_ownership=item.candidate_ownership,
                target_ownership=item.required_ownership,
                score_effect_status="modeled",
            )
        )
    return seeds


def _hard_seeds(evaluation: MatchEvaluation) -> list[_GapSeed]:
    seeds = []
    for item in evaluation.hard_constraint_results:
        if item.status in {"pass", "not_required"}:
            continue
        if item.status == "unresolved":
            gap_type: GapType = "unresolved_gap"
            category_reason = "HARD_REQUIREMENT_RESOLUTION_REQUIRED"
            severity = 0.5
        elif item.status == "unknown":
            gap_type = "evidence_gap"
            category_reason = "HARD_REQUIREMENT_EVIDENCE_GAP"
            severity = 0.4
        else:
            gap_type = "hard_constraint_gap"
            category_reason = "HARD_REQUIREMENT_GAP"
            severity = 1.0 if item.status == "fail" else 0.5
        seeds.append(
            _GapSeed(
                gap_type=gap_type,
                requirement_id=item.requirement_id,
                skill_id=None,
                current_level=item.candidate_value,
                target_level=item.required_value,
                status=item.status,
                reason_codes=(item.reason_code, category_reason),
                evidence=_dedupe_evidence(
                    item.position_evidence, item.candidate_evidence
                ),
                position_evidence_present=bool(item.position_evidence),
                candidate_evidence_present=bool(item.candidate_evidence),
                source_match_type="deterministic",
                transferable_skill_ids=(),
                prerequisite_skill_ids=(),
                transferability=0.0,
                mandatory=1.0,
                severity=severity,
                requirement_importance=1.0,
            )
        )
    return seeds


def _context_seed(item, gap_type: GapType, category_reason: str) -> _GapSeed | None:
    final_status = getattr(item, "status_detail", None) or item.match_status
    source_reason = item.reason_code
    if final_status != item.match_status:
        source_reason = {
            "matched": "RESPONSIBILITY_MATCHED",
            "partial": "RESPONSIBILITY_PARTIALLY_MATCHED",
            "not_observed": "RESPONSIBILITY_NOT_OBSERVED",
            "uncertain": "RESPONSIBILITY_UNKNOWN",
            "insufficient_evidence": "RESPONSIBILITY_UNKNOWN",
        }[final_status]
    if final_status == "matched":
        return None
    if final_status == "unresolved":
        selected_type: GapType = "unresolved_gap"
        reason = "CONTEXT_RESOLUTION_REQUIRED"
        severity = 0.5
    elif (
        final_status in {"unknown", "uncertain", "insufficient_evidence"}
        or item.match_type == "semantic_candidate"
    ):
        selected_type = "evidence_gap"
        reason = "CONTEXT_EVIDENCE_GAP"
        severity = 0.4
    else:
        selected_type = gap_type
        reason = category_reason
        severity = 1.0 if final_status == "not_observed" else 0.5
    return _GapSeed(
        gap_type=selected_type,
        requirement_id=item.requirement_id,
        skill_id=None,
        current_level=None,
        target_level=None,
        status=final_status,
        reason_codes=(source_reason, reason),
        evidence=_dedupe_evidence(item.position_evidence, item.candidate_evidence),
        position_evidence_present=bool(item.position_evidence),
        candidate_evidence_present=bool(item.candidate_evidence),
        source_match_type=item.match_type,
        transferable_skill_ids=(),
        prerequisite_skill_ids=(),
        transferability=0.0,
        mandatory=0.7,
        severity=severity,
        requirement_importance=0.8,
    )


def _context_seeds(evaluation: MatchEvaluation) -> list[_GapSeed]:
    seeds: list[_GapSeed] = []
    for item in evaluation.responsibility_results:
        seed = _context_seed(item, "responsibility_gap", "RESPONSIBILITY_GAP")
        if seed:
            seeds.append(seed)
    for item in evaluation.project_results:
        seed = _context_seed(item, "project_gap", "PROJECT_EXPERIENCE_GAP")
        if seed:
            seeds.append(seed)
    for item in evaluation.scenario_results:
        if item.scenario_type != "business_scenario":
            continue
        seed = _context_seed(item, "scenario_gap", "BUSINESS_SCENARIO_GAP")
        if seed:
            seeds.append(seed)
    return seeds


def _requirement_group_seeds(evaluation: MatchEvaluation) -> list[_GapSeed]:
    """Expose unsatisfied root operators as first-class planning blockers."""
    seeds = []
    for item in evaluation.requirement_group_results:
        if not item.is_root or item.status == "satisfied":
            continue
        score = item.score if item.score is not None else 0.0
        mandatory = 1.0 if item.priority == "required" else 0.7
        seeds.append(
            _GapSeed(
                gap_type="requirement_group_gap",
                requirement_id=item.group_id,
                skill_id=None,
                current_level=item.status,
                target_level="satisfied",
                status=item.status,
                reason_codes=(item.reason_code, "REQUIREMENT_GROUP_GAP"),
                evidence=item.position_evidence,
                position_evidence_present=bool(item.position_evidence),
                candidate_evidence_present=item.evaluable_count > 0,
                source_match_type="deterministic",
                transferable_skill_ids=(),
                prerequisite_skill_ids=(),
                transferability=0.0,
                mandatory=mandatory,
                severity=max(0.25, 1.0 - score),
                requirement_importance=mandatory,
            )
        )
    return seeds


def _impact(evaluation: MatchEvaluation, seed: _GapSeed) -> float:
    final = evaluation.final_match_result
    if final is None:
        return 0.0
    contributions = tuple(
        item for item in final.score_contributions if item.result_id == seed.requirement_id
    )
    known_impact = sum(
        item.effective_weight * (1.0 - (item.score_value or 0.0))
        for item in contributions
        if item.score_value is not None
    )
    if known_impact > 0:
        return min(1.0, known_impact * 3.0)
    relevant_dimensions = {item.dimension for item in contributions}
    fallback = sum(
        item.configured_weight / max(item.applicable_count, 1)
        for item in final.dimension_scores
        if item.dimension in relevant_dimensions
    )
    return min(1.0, fallback * 3.0)


def _low_dimension_boost(
    evaluation: MatchEvaluation,
    seed: _GapSeed,
    config: GapAnalysisConfig,
) -> float:
    """Extra priority for gaps that belong to currently low-scoring dimensions."""
    final = evaluation.final_match_result
    if final is None or config.low_dimension_boost <= 0:
        return 0.0
    low_scores = {
        item.dimension: item.score
        for item in final.dimension_scores
        if item.score is not None
        and item.effective_weight > 0
        and item.score < config.low_dimension_threshold
    }
    if not low_scores:
        return 0.0
    seed_dimensions = {
        item.dimension
        for item in final.score_contributions
        if item.result_id == seed.requirement_id
    }
    factors = [
        max(0.0, 1.0 - low_scores[dimension] / config.low_dimension_threshold)
        for dimension in seed_dimensions
        if dimension in low_scores
    ]
    if not factors:
        return 0.0
    return config.low_dimension_boost * max(factors)


def _priority_label(score: float, config: GapAnalysisConfig) -> GapPriority:
    if score >= config.critical_threshold:
        return "critical"
    if score >= config.high_threshold:
        return "high"
    if score >= config.medium_threshold:
        return "medium"
    return "low"


def _prioritize(
    evaluation: MatchEvaluation,
    seeds: tuple[_GapSeed, ...],
    config: GapAnalysisConfig,
) -> tuple[PrioritizedGap, ...]:
    output = []
    for seed in seeds:
        prerequisite = 1.0 if seed.source_match_type == "prerequisite" else 0.0
        evidence_incomplete = float(
            not seed.position_evidence_present or not seed.candidate_evidence_present
        )
        evidence_reason_codes = (
            *(("POSITION_EVIDENCE_MISSING",) if not seed.position_evidence_present else ()),
            *(("CANDIDATE_EVIDENCE_MISSING",) if not seed.candidate_evidence_present else ()),
        )
        raw = (
            config.required_factor_weight * seed.mandatory
            + config.severity_weight * seed.severity
            + config.score_impact_weight * _impact(evaluation, seed)
            + config.requirement_weight * seed.requirement_importance
            + config.prerequisite_weight * prerequisite
            + config.evidence_weight * evidence_incomplete
            + _low_dimension_boost(evaluation, seed, config)
            - config.transferable_reduction_weight * seed.transferability
        )
        score = round(max(0.0, min(1.0, raw)) * 100, 4)
        output.append(
            PrioritizedGap(
                gap_type=seed.gap_type,
                requirement_id=seed.requirement_id,
                skill_id=seed.skill_id,
                current_level=seed.current_level,
                target_level=seed.target_level,
                priority=_priority_label(score, config),
                priority_score=score,
                reason_codes=tuple(
                    dict.fromkeys((*seed.reason_codes, *evidence_reason_codes))
                ),
                evidence=seed.evidence,
                position_evidence_present=seed.position_evidence_present,
                candidate_evidence_present=seed.candidate_evidence_present,
                source_match_type=seed.source_match_type,
                transferable_skill_ids=seed.transferable_skill_ids,
                prerequisite_skill_ids=seed.prerequisite_skill_ids,
                transferability_score=seed.transferability,
                current_ownership=seed.current_ownership,
                target_ownership=seed.target_ownership,
                score_effect_status=seed.score_effect_status,
            )
        )
    return tuple(
        sorted(
            output,
            key=lambda item: (-item.priority_score, item.requirement_id, item.gap_type),
        )
    )


def _step_template(gap: PrioritizedGap) -> LearningStep:
    skill_name = gap.skill_id or gap.requirement_id
    basis = tuple(
        [
            *(f"reason:{code}" for code in gap.reason_codes),
            *(f"evidence:{item.source_id}" for item in gap.evidence),
        ]
    )
    if gap.gap_type == "hard_constraint_gap":
        objective = (
            f"满足硬性条件 {gap.requirement_id}，达到 {gap.target_level or '要求值'}"
        )
        estimated_hours = 2.0
        criteria = (
            f"requirement_status:pass:{gap.requirement_id}",
            f"evidence_linked:{gap.requirement_id}",
        )
    elif gap.gap_type == "required_skill_missing":
        objective = (
            f"达到 {skill_name} 的 {gap.target_level or 'required'} 水平"
            "并补充可验证项目证据"
        )
        estimated_hours = 6.0 if gap.transferable_skill_ids else 8.0
        criteria = (
            f"skill_observed:{gap.skill_id or gap.requirement_id}",
            f"demonstrated_level_at_least:{gap.target_level or 'required'}",
            f"evidence_linked:{gap.requirement_id}",
        )
    elif gap.gap_type == "skill_level_gap":
        objective = (
            f"将 {skill_name} 从 "
            f"{gap.current_level or 'unknown'} to {gap.target_level or 'required'}"
        )
        estimated_hours = 5.0
        criteria = (
            f"demonstrated_level_at_least:{gap.target_level or 'required'}",
            f"evidence_linked:{gap.requirement_id}",
        )
    elif gap.gap_type in {"evidence_gap", "usage_evidence_gap"}:
        objective = f"为 {skill_name} 补充可核验的证据片段"
        estimated_hours = 2.0
        criteria = (f"evidence_linked:{gap.requirement_id}",)
    elif gap.gap_type == "ownership_gap":
        objective = (
            f"为 {skill_name} 补充从 {gap.current_ownership or 'unknown'} "
            f"到 {gap.target_ownership or 'required'} 的独立负责证据"
        )
        estimated_hours = 4.0
        criteria = (
            f"ownership_at_least:{gap.target_ownership or 'required'}",
            f"evidence_linked:{gap.requirement_id}",
        )
    elif gap.gap_type == "responsibility_gap":
        objective = f"补充 {gap.requirement_id} 对应的职责经历与证据"
        estimated_hours = 4.0
        criteria = (
            f"requirement_status:matched_or_partial:{gap.requirement_id}",
            f"evidence_linked:{gap.requirement_id}",
        )
    elif gap.gap_type in {"project_gap", "scenario_gap"}:
        objective = f"补充 {gap.requirement_id} 对应的可验证项目或业务场景经历"
        estimated_hours = 5.0
        criteria = (
            f"requirement_status:matched_or_partial:{gap.requirement_id}",
            f"evidence_linked:{gap.requirement_id}",
        )
    else:
        objective = f"解决 {gap.requirement_id} 的画像信息解析"
        estimated_hours = 1.0
        criteria = (f"resolution_status:resolved:{gap.requirement_id}",)
    prerequisites = gap.prerequisite_skill_ids
    return LearningStep(
        step_order=1,
        target_skill_id=gap.skill_id,
        objective=objective,
        prerequisite_skill_ids=prerequisites,
        basis=basis,
        estimated_hours=estimated_hours,
        cost_source_type="heuristic",
        cost_source_ref="gap-learning-hours.v1",
        estimate_status="estimated",
        cost_model="gap-learning-hours.v1",
        completion_criteria=criteria,
        source_requirement_ids=(gap.requirement_id,),
        reason_codes=gap.reason_codes,
    )


def _order_steps(
    gaps: tuple[PrioritizedGap, ...],
) -> tuple[LearningStep, ...]:
    templates = tuple((gap, _step_template(gap)) for gap in gaps)
    pending = list(templates)
    ordered: list[LearningStep] = []
    target_ids = {step.target_skill_id for _, step in templates if step.target_skill_id}
    completed_targets: set[str] = set()
    while pending:
        ready = tuple(
            item
            for item in pending
            if not (
                set(item[1].prerequisite_skill_ids).intersection(target_ids)
                - completed_targets
            )
        )
        if not ready:
            # No topological progress is possible: every remaining internal
            # prerequisite depends on another pending target. Preserve all
            # steps for inspection, but never present the cycle as executable.
            for _, step in sorted(
                pending,
                key=lambda item: (-item[0].priority_score, item[0].requirement_id),
            ):
                ordered.append(
                    step.model_copy(
                        update={
                            "step_order": len(ordered) + 1,
                            "planning_status": "blocked",
                            "blocked_reason_codes": ("PREREQUISITE_CYCLE",),
                        }
                    )
                )
            break
        candidates = ready
        selected = min(
            candidates,
            key=lambda item: (-item[0].priority_score, item[0].requirement_id),
        )
        pending.remove(selected)
        _, step = selected
        ordered.append(step.model_copy(update={"step_order": len(ordered) + 1}))
        if step.target_skill_id:
            completed_targets.add(step.target_skill_id)
    return tuple(ordered)


def _counterfactual_suggestions(
    gaps: tuple[PrioritizedGap, ...],
) -> tuple[CounterfactualSuggestion, ...]:
    suggestions = []
    for gap in gaps:
        if gap.gap_type == "hard_constraint_gap":
            suggestion = (
                f"在继续补齐技能前，先满足 {gap.requirement_id} 的硬性条件；"
                "否则即使技能分提高，整体推荐仍受 Hard Gate 限制。"
            )
        elif gap.transferable_skill_ids:
            suggestion = (
                f"优先利用已有技能 {', '.join(gap.transferable_skill_ids)} "
                f"迁移到 {gap.skill_id or gap.requirement_id}，"
                "用可验证项目证明迁移能力，再补齐目标技能差异。"
            )
        elif gap.gap_type in {"evidence_gap", "usage_evidence_gap"}:
            suggestion = (
                f"先补充 {gap.skill_id or gap.requirement_id} 的原文与项目证据，"
                "而不是继续扩大学习范围。"
            )
        elif gap.gap_type == "ownership_gap":
            suggestion = (
                f"补充 {gap.skill_id or gap.requirement_id} 的"
                f"{gap.target_ownership or 'ownership'} 级可核验职责证据；"
                "v1 只展示证据收益，不改变正式分数。"
            )
        else:
            suggestion = (
                f"优先完成 {gap.skill_id or gap.requirement_id} 的优先级最高步骤，"
                "再处理下一层依赖。"
            )
        suggestions.append(
            CounterfactualSuggestion(
                requirement_id=gap.requirement_id,
                skill_id=gap.skill_id,
                suggestion=suggestion,
                basis_evidence=gap.evidence,
            )
        )
    return tuple(suggestions)


def build_gap_analysis(
    evaluation: MatchEvaluation,
    config: GapAnalysisConfig | None = None,
    *,
    time_budget_hours: float | None = None,
    include_learning_steps: bool = True,
) -> GapAnalysis:
    """Generate grounded gaps, counterfactual advice and a budgeted learning path."""
    selected = config or GapAnalysisConfig()
    seeds = tuple(
        _hard_seeds(evaluation)
        + _skill_seeds(evaluation, selected)
        + _context_seeds(evaluation)
        + _requirement_group_seeds(evaluation)
    )
    gaps = _prioritize(evaluation, seeds, selected)
    # The formal Learning Path must be derived from the selected Minimal Action
    # Set, not from a gap-only fixed-hour template. The legacy template remains
    # available for offline competition metrics only.
    learning_path = _order_steps(gaps) if include_learning_steps else ()
    final = evaluation.final_match_result
    readiness = None
    if final is not None and final.overall_score is not None:
        readiness = final.overall_score / 100 * final.match_confidence
        if final.hard_gate_status == "failed":
            readiness = min(readiness, selected.hard_gate_readiness_cap)
        readiness = round(readiness, 6)
    return GapAnalysis(
        generation_status="completed",
        prioritized_gaps=gaps,
        learning_path=learning_path,
        counterfactual_suggestions=_counterfactual_suggestions(gaps),
        time_budget_hours=time_budget_hours,
        over_budget=bool(
            time_budget_hours is not None
            and sum(step.estimated_hours for step in learning_path) > time_budget_hours
        ),
        estimated_readiness=readiness,
        profile_references=ProfileReferences(
            cv_profile_id=evaluation.cv_profile_id,
            cv_profile_version=evaluation.cv_profile_version,
            position_profile_id=evaluation.position_profile_id,
            position_profile_version=evaluation.position_profile_version,
        ),
        algorithm_version=selected.algorithm_version,
        config_version=selected.config_version,
        gap_policy_version=selected.gap_policy_version,
        gap_policy_hash=gap_policy_hash(selected),
        source_evaluation_algorithm_version=evaluation.algorithm_version,
        source_scoring_algorithm_version=(final.algorithm_version if final else None),
        source_scoring_config_version=(final.scoring_config_version if final else None),
        semantic_algorithm_version=evaluation.semantic_algorithm_version,
        embedding_version=evaluation.embedding_version,
    )
