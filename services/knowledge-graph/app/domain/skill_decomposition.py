"""A20: 技能变化语义/拓扑双分解 (Semantic-Topological Decomposition)

将技能在两个 GraphVersion 之间的权重变化分解为三路信号：
- 语义漂移 (semantic drift): 类别重分类、名称变化、上下文相似度
- 拓扑变化 (topological change): 邻居 Jaccard、社区迁移
- 证据变化 (evidence change): source/enterprise/sample 统计量变化
- 残差 (residual): 以上不能解释的真实市场变化比例

约束：
- 缺失维度显式标记 unavailable，不合成虚假分解
- 残差不是"错误"，而是"未被覆盖/语义/证据解释的市场信号"
- 每项变化可回跳 before/after Release 和 Evidence
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass
class SemanticContext:
    """技能的语义上下文。"""
    category_code: str | None
    canonical_name: str
    responsibility_token_count: int  # related position-level responsibility tokens


@dataclass
class TopologicalContext:
    """技能的拓扑上下文。"""
    same_category_skills: set[str] = field(default_factory=set)
    category_rank: int = 0  # position of this skill within its category (by weight)
    modality: str = "required"
    importance_level: str = "common"


@dataclass
class EvidenceContext:
    """技能的支撑证据分布。"""
    support_document_count: int = 0
    source_diversity: int = 0
    enterprise_coverage: int = 0
    total_jds: int = 1


@dataclass
class SkillDecomposition:
    """单个技能的变化分解结果。"""

    skill_id: str
    canonical_name_before: str
    canonical_name_after: str
    weight_before: float
    weight_after: float
    weight_delta: float

    # === 语义维 ===
    context_similarity: float  # 0..1, semantic context overlap
    category_changed: bool
    name_changed: bool

    # === 拓扑维 ===
    neighborhood_jaccard: float  # 0..1, co-occurring neighbor overlap
    community_migrated: bool  # category_code changed

    # === 证据维 ===
    evidence_delta_normalized: float  # normalized evidence change magnitude

    # === 归因 ===
    semantic_contribution: float  # proportion of |weight_delta| attributed to semantic
    topological_contribution: float
    evidence_contribution: float
    residual: float  # unexplained (genuine market signal)

    explanation: str = ""

    @property
    def dominant_factor(self) -> str:
        contributions = {
            "semantic": self.semantic_contribution,
            "topological": self.topological_contribution,
            "evidence": self.evidence_contribution,
            "residual": self.residual,
        }
        return max(contributions, key=contributions.get)

    @property
    def is_explained_by_artifact(self) -> bool:
        """是否主要由语义/证据/拓扑伪影驱动（非真实市场变化）。"""
        return (
            self.semantic_contribution
            + self.topological_contribution
            + self.evidence_contribution
        ) > self.residual


def decompose_skill_change(
    before_relation: Mapping[str, object],
    after_relation: Mapping[str, object],
    before_all_skills: list[Mapping[str, object]],
    after_all_skills: list[Mapping[str, object]],
    before_total_jds: int = 1,
    after_total_jds: int = 1,
) -> SkillDecomposition:
    """对单个技能在两个版本间的变化进行语义/拓扑/证据三路分解。

    Args:
        before_relation: 前一版本的 skill_relation
        after_relation: 后一版本的 skill_relation
        before_all_skills: 前一版本全部技能 relations
        after_all_skills: 后一版本全部技能 relations
        before_total_jds: 前一版本总 JD 数
        after_total_jds: 后一版本总 JD 数

    Returns:
        SkillDecomposition with attribution breakdown
    """
    skill_id = str(before_relation.get("skill_id", ""))
    name_before = str(before_relation.get("canonical_name", ""))
    name_after = str(after_relation.get("canonical_name", ""))

    w_before = float(before_relation.get("weight", before_relation.get("final_weight", 0)))
    w_after = float(after_relation.get("weight", after_relation.get("final_weight", 0)))
    w_delta = w_after - w_before

    cat_before = str(before_relation.get("category_code", ""))
    cat_after = str(after_relation.get("category_code", ""))
    category_changed = cat_before != cat_after and cat_before != "" and cat_after != ""
    name_changed = name_before != name_after

    # === 语义维 ===
    context_similarity = _compute_context_similarity(
        cat_before, cat_after, name_before, name_after,
    )
    context_drift = 1.0 - context_similarity

    # === 拓扑维 ===
    neighbors_before = _category_neighbors(cat_before, before_all_skills, skill_id)
    neighbors_after = _category_neighbors(cat_after, after_all_skills, skill_id)
    neighborhood_jaccard = _jaccard(neighbors_before, neighbors_after)
    community_migrated = category_changed  # 类别变化 = 社区迁移

    # === 证据维 ===
    stats_before = before_relation.get("statistics", {})
    stats_after = after_relation.get("statistics", {})
    evidence_delta_normalized = _compute_evidence_delta(
        stats_before, stats_after, before_total_jds, after_total_jds,
    )

    # === 归因 ===
    # 三个信号对 |weight_delta| 的相对贡献
    abs_delta = abs(w_delta) if abs(w_delta) > 0.001 else 0.001

    # semantic: context_drift weighted by how much of the delta it could explain
    sem_raw = context_drift * abs_delta
    # topological: (1 - jaccard) weighted
    topo_raw = (1.0 - neighborhood_jaccard) * abs_delta * 0.8  # topology less direct
    # evidence: normalized evidence change weighted
    evid_raw = evidence_delta_normalized * abs_delta
    # bonus for discrete events
    sem_raw += (0.15 * abs_delta) if category_changed else 0.0
    sem_raw += (0.08 * abs_delta) if name_changed else 0.0

    total_raw = sem_raw + topo_raw + evid_raw
    if total_raw < 0.0001:
        # Nothing explains — all residual
        semantic_contribution = 0.0
        topological_contribution = 0.0
        evidence_contribution = 0.0
        residual = 1.0
    else:
        # Residual is capped: at most 1.0, and at least the unexplained portion
        max_explained = min(total_raw / abs_delta, 0.95)  # at least 5% residual floor
        scale = max_explained / total_raw * abs_delta if total_raw > 0 else 0.0
        semantic_contribution = round(sem_raw / total_raw * max_explained, 4)
        topological_contribution = round(topo_raw / total_raw * max_explained, 4)
        evidence_contribution = round(evid_raw / total_raw * max_explained, 4)
        residual = round(1.0 - max_explained, 4)

    # === 解释文本 ===
    parts: list[str] = []
    if category_changed:
        parts.append(f"category migrated: {cat_before} → {cat_after}")
    if name_changed:
        parts.append(f"name changed: '{name_before}' → '{name_after}'")
    if neighborhood_jaccard < 0.5:
        parts.append(f"neighborhood Jaccard={neighborhood_jaccard:.2f} (low overlap)")
    if evidence_delta_normalized > 0.15:
        parts.append(
            f"evidence delta={evidence_delta_normalized:.2f} "
            f"(src:{stats_after.get('source_diversity',0)}-{stats_before.get('source_diversity',0)}, "
            f"ent:{stats_after.get('enterprise_coverage',0)}-{stats_before.get('enterprise_coverage',0)}, "
            f"smp:{stats_after.get('support_document_count',0)}-{stats_before.get('support_document_count',0)})"
        )
    explanation = "; ".join(parts) if parts else "unexplained market signal"

    return SkillDecomposition(
        skill_id=skill_id,
        canonical_name_before=name_before,
        canonical_name_after=name_after,
        weight_before=round(w_before, 4),
        weight_after=round(w_after, 4),
        weight_delta=round(w_delta, 4),
        context_similarity=round(context_similarity, 4),
        category_changed=category_changed,
        name_changed=name_changed,
        neighborhood_jaccard=round(neighborhood_jaccard, 4),
        community_migrated=community_migrated,
        evidence_delta_normalized=round(evidence_delta_normalized, 4),
        semantic_contribution=semantic_contribution,
        topological_contribution=topological_contribution,
        evidence_contribution=evidence_contribution,
        residual=residual,
        explanation=explanation,
    )


def decompose_version_pair(
    before_snapshot: Mapping[str, object],
    after_snapshot: Mapping[str, object],
) -> list[SkillDecomposition]:
    """对两个版本之间所有共同技能进行分解。

    Args:
        before_snapshot: 前一版本快照 (含 skill_relations, sample_stats)
        after_snapshot: 后一版本快照

    Returns:
        各共同技能（两版本均有）的 Decomposition 列表，
        按 |weight_delta| 降序排列
    """
    before_skills = before_snapshot.get("skill_relations", [])
    after_skills = after_snapshot.get("skill_relations", [])

    # Index after by skill_id
    after_map: dict[str, dict] = {}
    for sr in after_skills:
        sid = str(sr.get("skill_id", ""))
        after_map[sid] = sr

    before_total = int(before_snapshot.get("sample_stats", {}).get("included_samples", 1))
    after_total = int(after_snapshot.get("sample_stats", {}).get("included_samples", 1))

    results: list[SkillDecomposition] = []
    for sr_before in before_skills:
        sid = str(sr_before.get("skill_id", ""))
        if sid not in after_map:
            continue
        d = decompose_skill_change(
            sr_before, after_map[sid],
            before_skills, after_skills,
            before_total, after_total,
        )
        if abs(d.weight_delta) >= 0.005:
            results.append(d)

    results.sort(key=lambda x: abs(x.weight_delta), reverse=True)
    return results


def compute_pair_summary(
    decompositions: list[SkillDecomposition],
) -> Mapping[str, int | float]:
    """计算一个版本对的分解汇总统计。"""
    n = len(decompositions)
    if n == 0:
        return {"total_skills_compared": 0}

    artifact_driven = [d for d in decompositions if d.is_explained_by_artifact]
    market_driven = [d for d in decompositions if not d.is_explained_by_artifact]
    migrations = [d for d in decompositions if d.community_migrated]
    renames = [d for d in decompositions if d.name_changed]

    avg = lambda vals: round(sum(vals) / len(vals), 4) if vals else 0.0

    return {
        "total_skills_compared": n,
        "artifact_driven_count": len(artifact_driven),
        "market_driven_count": len(market_driven),
        "community_migration_count": len(migrations),
        "name_change_count": len(renames),
        "avg_semantic_contribution": avg([d.semantic_contribution for d in decompositions]),
        "avg_topological_contribution": avg([d.topological_contribution for d in decompositions]),
        "avg_evidence_contribution": avg([d.evidence_contribution for d in decompositions]),
        "avg_residual": avg([d.residual for d in decompositions]),
        "avg_neighborhood_jaccard": avg([d.neighborhood_jaccard for d in decompositions]),
        "avg_context_similarity": avg([d.context_similarity for d in decompositions]),
        "avg_abs_weight_delta": avg([abs(d.weight_delta) for d in decompositions]),
    }


# ===== private helpers =====

def _category_neighbors(
    category: str,
    all_skills: list[dict],
    exclude_skill_id: str,
) -> set[str]:
    """获取同一 category 内其他技能 ID。"""
    return {
        str(sr.get("skill_id", ""))
        for sr in all_skills
        if str(sr.get("category_code", "")) == category
        and str(sr.get("skill_id", "")) != exclude_skill_id
    }


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0  # both empty → perfect agreement
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _compute_context_similarity(
    cat_before: str,
    cat_after: str,
    name_before: str,
    name_after: str,
) -> float:
    """计算语义上下文相似度（0..1）。

    基于类别一致性和名称稳定性。
    """
    score = 0.0
    # category match: 50% weight
    if cat_before and cat_after:
        if cat_before == cat_after:
            score += 0.5
    else:
        score += 0.25  # one side unknown, partial credit
    # name match: 50% weight
    if name_before and name_after:
        if name_before == name_after:
            score += 0.5
        else:
            # partial: check if one is substring of another
            nl = name_before.lower()
            na = name_after.lower()
            if nl in na or na in nl:
                score += 0.25
    else:
        score += 0.25  # one side unknown
    return score


def _compute_evidence_delta(
    stats_before: dict,
    stats_after: dict,
    before_total_jds: int,
    after_total_jds: int,
) -> float:
    """计算归一化证据变化量（0..1）。"""
    def _get(key: str, stats: dict) -> float:
        return float(stats.get(key, 0))

    src_b = _get("source_diversity", stats_before)
    src_a = _get("source_diversity", stats_after)
    ent_b = _get("enterprise_coverage", stats_before)
    ent_a = _get("enterprise_coverage", stats_after)
    sup_b = _get("support_document_count", stats_before)
    sup_a = _get("support_document_count", stats_after)

    # Normalize each to [0, 1]
    max_src = max(src_b, src_a, 1)
    max_ent = max(ent_b, ent_a, 1)
    max_sup = max(sup_b, sup_a, max(before_total_jds, after_total_jds, 1))

    deltas = [
        abs(src_a - src_b) / max_src,
        abs(ent_a - ent_b) / max_ent,
        abs(sup_a - sup_b) / max_sup,
    ]
    return round(sum(deltas) / 3.0, 4)


# ===== cross-pair analysis =====

def compute_cross_pair_analysis(
    all_pair_decompositions: list[tuple[str, list[SkillDecomposition]]],
) -> Mapping[str, object]:
    """跨版本对分析：追踪同一技能在多个版本对中的主导因子变化。

    Args:
        all_pair_decompositions: [(pair_label, decompositions), ...]

    Returns:
        每个技能在各版本对中的归因追踪
    """
    skill_trace: dict[str, list[dict]] = {}
    for pair_label, decomps in all_pair_decompositions:
        for d in decomps:
            if d.skill_id not in skill_trace:
                skill_trace[d.skill_id] = []
            skill_trace[d.skill_id].append({
                "pair": pair_label,
                "canonical_name": d.canonical_name_after,
                "weight_delta": d.weight_delta,
                "dominant_factor": d.dominant_factor,
                "semantic": d.semantic_contribution,
                "topological": d.topological_contribution,
                "evidence": d.evidence_contribution,
                "residual": d.residual,
                "community_migrated": d.community_migrated,
                "name_changed": d.name_changed,
            })

    # Stability: does the same skill consistently have the same dominant factor?
    consistent_count = 0
    variable_count = 0
    for sid, trace in skill_trace.items():
        if len(trace) < 2:
            continue
        dominants = {t["dominant_factor"] for t in trace}
        if len(dominants) == 1:
            consistent_count += 1
        else:
            variable_count += 1

    # Skills that migrated community at least once
    migratory_skills = [
        {
            "skill_id": sid,
            "name": trace[0]["canonical_name"] if trace else "",
            "migration_count": sum(1 for t in trace if t["community_migrated"]),
            "trace": trace,
        }
        for sid, trace in skill_trace.items()
        if any(t["community_migrated"] for t in trace)
    ]

    return {
        "skills_tracked": len(skill_trace),
        "consistent_dominant_factor": consistent_count,
        "variable_dominant_factor": variable_count,
        "migratory_skills": sorted(
            migratory_skills, key=lambda x: x["migration_count"], reverse=True,
        ),
        "skill_traces": {
            sid: trace for sid, trace in sorted(
                skill_trace.items(),
                key=lambda x: sum(abs(t["weight_delta"]) for t in x[1]),
                reverse=True,
            )[:20]
        },
    }
