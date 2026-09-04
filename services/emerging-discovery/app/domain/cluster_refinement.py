"""Deterministic cluster split refinement for over-merged clusters.

The refinement is a post-clustering challenger.  It never consumes Gold
candidate IDs or downstream identity assignments.  It only reads the evidence
inside each cluster (responsibilities, skills, titles, companies, temporal
metadata) and splits a cluster when two internally coherent subgroups with an
obvious responsibility/skill contradiction are present.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from app.domain.discovery import AlgorithmCluster, JDSnapshot
from app.domain.values import FrozenDict


REFINEMENT_ALGORITHM_VERSION = "deterministic-cluster-split-refinement-v1"
REFINEMENT_ALGORITHM_VERSION_V2 = "deterministic-cluster-split-refinement-v2"

# Common tooling and foundation-model skills.  Tool substitution is explicitly
# not allowed to drive a split, so these skills only corroborate responsibility
# evidence and never count as discriminative on their own.
GENERIC_SKILLS = frozenset(
    {
        "java",
        "python",
        "go",
        "golang",
        "c",
        "c++",
        "cpp",
        "rust",
        "scala",
        "shell",
        "sql",
        "spring",
        "spring boot",
        "springcloud",
        "mysql",
        "postgresql",
        "redis",
        "kafka",
        "docker",
        "kubernetes",
        "k8s",
        "pytorch",
        "tensorflow",
        "transformer",
        "llm",
        "大模型",
        "nlp",
        "rag",
        "机器学习",
        "深度学习",
        "多模态",
        "微调",
        "预训练",
        "微服务",
    }
)

# Compact role-family vocabulary derived from responsibility wording.  This is
# intentionally generic: it describes job families, not formal sample IDs, and
# is used only to generate candidate subgroup partitions.
ROLE_FAMILIES: Mapping[str, tuple[str, ...]] = {
    "product_operations": (
        "产品",
        "产品经理",
        "产品规划",
        "产品设计",
        "运营",
        "标注",
        "用户研究",
        "需求分析",
        "平台规划",
        "策略产品",
        "数据产品",
        "评测体系",
        "ab测试",
    ),
    "algorithm_model": (
        "算法",
        "模型训练",
        "预训练",
        "微调",
        "sft",
        "rlhf",
        "强化学习",
        "对齐",
        "多模态",
        "自然语言",
        "内容理解",
        "意图识别",
        "推荐",
        "广告",
        "搜索",
        "生成模型",
        "扩散模型",
        "数据清洗",
        "数据合成",
        "评测",
        "embedding",
    ),
    "model_inference": (
        "推理引擎",
        "推理服务",
        "推理",
        "模型压缩",
        "蒸馏",
        "异构计算",
        "maas",
        "资源调度",
        "性能优化",
    ),
    "agent_application": (
        "agent",
        "智能体",
        "工具调用",
        "function calling",
        "rag",
        "检索增强",
        "知识库",
        "对话",
        "任务分解",
        "规划决策",
        "多智能体",
        "工作流",
        "编排",
        "mcp",
        "skill",
        "prompt",
        "应用层",
        "全栈",
        "前端",
    ),
    "data_infrastructure": (
        "数据平台",
        "数据引擎",
        "数仓",
        "olap",
        "实时计算",
        "存储",
        "数据库",
        "血缘",
        "数据治理",
        "中间件",
        "调度",
        "集群",
        "容器",
        "微服务",
        "高可用",
        "架构设计",
        "运维",
        "ci/cd",
        "后端",
        "服务端",
    ),
    "robotics_embodied": (
        "机器人",
        "具身",
        "机械臂",
        "底盘",
        "运动规划",
        "导航",
        "抓取",
        "vlm",
        "视觉语言",
        "座舱",
        "智能驾驶",
        "自动驾驶",
        "motion primitives",
    ),
    "domain_business": (
        "医疗",
        "健康",
        "问诊",
        "医生",
        "电商",
        "社区",
        "内容",
        "视频",
        "游戏",
        "客服",
        "供应链",
        "制造",
        "本地生活",
        "点评",
        "教育",
        "金融",
    ),
}


@dataclass(frozen=True)
class SplitRefinementConfig:
    min_split_members: int = 6
    min_subgroup_support: int = 2
    max_average_combined_similarity: float = 0.30
    max_average_responsibility_similarity: float = 0.30
    min_subgroup_combined_coherence: float = 0.16
    min_responsibility_separation: float = 0.85
    min_discriminative_skills: int = 1
    skill_concentration_ratio: float = 2.0
    min_contradiction_terms: int = 1
    min_intra_minus_inter: float = 0.0
    responsibility_weight: float = 0.6
    skill_weight: float = 0.4
    anti_over_split_guards: bool = False
    company_partition_requires_contradiction: bool = False
    sparse_responsibility_min_tokens: int = 12

    @property
    def algorithm_version(self) -> str:
        if self.anti_over_split_guards:
            return REFINEMENT_ALGORITHM_VERSION_V2
        return REFINEMENT_ALGORITHM_VERSION

    @classmethod
    def from_json(cls, value: Mapping[str, Any] | None) -> "SplitRefinementConfig":
        data = dict(value or {})
        return cls(
            min_split_members=int(data.get("min_split_members", 6)),
            min_subgroup_support=int(data.get("min_subgroup_support", 2)),
            max_average_combined_similarity=float(
                data.get("max_average_combined_similarity", 0.30)
            ),
            max_average_responsibility_similarity=float(
                data.get("max_average_responsibility_similarity", 0.30)
            ),
            min_subgroup_combined_coherence=float(
                data.get("min_subgroup_combined_coherence", 0.16)
            ),
            min_responsibility_separation=float(
                data.get("min_responsibility_separation", 0.85)
            ),
            min_discriminative_skills=int(data.get("min_discriminative_skills", 1)),
            skill_concentration_ratio=float(data.get("skill_concentration_ratio", 2.0)),
            min_contradiction_terms=int(data.get("min_contradiction_terms", 1)),
            min_intra_minus_inter=float(data.get("min_intra_minus_inter", 0.0)),
            responsibility_weight=float(data.get("responsibility_weight", 0.6)),
            skill_weight=float(data.get("skill_weight", 0.4)),
            anti_over_split_guards=bool(
                data.get("anti_over_split_guards", False)
            ),
            company_partition_requires_contradiction=bool(
                data.get("company_partition_requires_contradiction", False)
            ),
            sparse_responsibility_min_tokens=int(
                data.get("sparse_responsibility_min_tokens", 12)
            ),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "min_split_members": self.min_split_members,
            "min_subgroup_support": self.min_subgroup_support,
            "max_average_combined_similarity": self.max_average_combined_similarity,
            "max_average_responsibility_similarity": (
                self.max_average_responsibility_similarity
            ),
            "min_subgroup_combined_coherence": self.min_subgroup_combined_coherence,
            "min_responsibility_separation": self.min_responsibility_separation,
            "min_discriminative_skills": self.min_discriminative_skills,
            "skill_concentration_ratio": self.skill_concentration_ratio,
            "min_contradiction_terms": self.min_contradiction_terms,
            "min_intra_minus_inter": self.min_intra_minus_inter,
            "responsibility_weight": self.responsibility_weight,
            "skill_weight": self.skill_weight,
            "anti_over_split_guards": self.anti_over_split_guards,
            "company_partition_requires_contradiction": (
                self.company_partition_requires_contradiction
            ),
            "sparse_responsibility_min_tokens": (
                self.sparse_responsibility_min_tokens
            ),
        }


@dataclass(frozen=True)
class _MemberFeatures:
    jd_id: str
    title: str
    company: str | None
    publish_date: date | None
    window_id: str
    responsibility_text: str
    skill_set: frozenset[str]
    families: frozenset[str]


@dataclass(frozen=True)
class ClusterCoherenceDiagnostics:
    cluster_id: str
    member_count: int
    title_family_coherence: float
    title_distinct_ratio: float | None
    responsibility_coherence: float
    skill_coherence: float
    combined_coherence: float
    pairwise_similarity: Mapping[str, float]
    company_distribution: Mapping[str, int]
    source_distribution: Mapping[str, int]
    company_hhi: float
    responsibility_contradiction_terms: int
    discriminative_skill_count: int
    candidate_subgroup_count: int
    temporal_compatibility: Mapping[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "member_count": self.member_count,
            "title_family_coherence": self.title_family_coherence,
            "title_distinct_ratio": self.title_distinct_ratio,
            "responsibility_coherence": self.responsibility_coherence,
            "skill_coherence": self.skill_coherence,
            "combined_coherence": self.combined_coherence,
            "pairwise_similarity": dict(self.pairwise_similarity),
            "company_distribution": dict(self.company_distribution),
            "source_distribution": dict(self.source_distribution),
            "company_hhi": self.company_hhi,
            "responsibility_contradiction_terms": (
                self.responsibility_contradiction_terms
            ),
            "discriminative_skill_count": self.discriminative_skill_count,
            "candidate_subgroup_count": self.candidate_subgroup_count,
            "temporal_compatibility": dict(self.temporal_compatibility),
        }


@dataclass(frozen=True)
class SplitRefinementRecord:
    parent_cluster_id: str
    child_cluster_ids: tuple[str, ...]
    member_assignment: Mapping[str, tuple[str, ...]]
    split_trigger: tuple[str, ...]
    title_evidence: Mapping[str, Any]
    responsibility_evidence: Mapping[str, Any]
    skill_evidence: Mapping[str, Any]
    subgroup_coherence: Mapping[str, Any]
    inter_group_contradiction: Mapping[str, Any]
    confidence: float
    algorithm_version: str

    def to_json(self) -> dict[str, Any]:
        return {
            "parent_cluster_id": self.parent_cluster_id,
            "child_cluster_ids": list(self.child_cluster_ids),
            "member_assignment": {
                key: list(value) for key, value in self.member_assignment.items()
            },
            "split_trigger": list(self.split_trigger),
            "title_evidence": dict(self.title_evidence),
            "responsibility_evidence": dict(self.responsibility_evidence),
            "skill_evidence": dict(self.skill_evidence),
            "subgroup_coherence": dict(self.subgroup_coherence),
            "inter_group_contradiction": dict(self.inter_group_contradiction),
            "confidence": self.confidence,
            "algorithm_version": self.algorithm_version,
        }


@dataclass(frozen=True)
class RefinementOutcome:
    clusters: tuple[AlgorithmCluster, ...]
    diagnostics: tuple[ClusterCoherenceDiagnostics, ...]
    splits: tuple[SplitRefinementRecord, ...]


def _tokens(text: str) -> list[str]:
    lowered = text.casefold()
    words = re.findall(r"[a-z0-9_+#.-]+|[\u4e00-\u9fff]{2,}", lowered)
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    words.extend(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return words


def _tfidf(
    documents: Sequence[str],
) -> tuple[tuple[float, ...], ...]:
    tokenized = [_tokens(value) for value in documents]
    sets = [set(value) for value in tokenized]
    vocabulary = sorted({token for values in sets for token in values})
    frequencies = Counter(token for values in sets for token in values)
    rows: list[list[float]] = []
    for document in tokenized:
        counts = Counter(document)
        row: list[float] = []
        for token in vocabulary:
            count = counts.get(token, 0)
            if count:
                row.append(
                    (count / max(len(document), 1))
                    * (math.log((1 + len(documents)) / (1 + frequencies[token])) + 1)
                )
            else:
                row.append(0.0)
        norm = math.sqrt(sum(value * value for value in row))
        rows.append([value / norm for value in row] if norm else row)
    return tuple(tuple(round(value, 12) for value in row) for row in rows)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _skills(snapshot: JDSnapshot) -> frozenset[str]:
    values = {
        str(item.identity).strip().casefold()
        for item in snapshot.structured_data.required_skills
        + snapshot.structured_data.bonus_skills
        if item.identity and str(item.identity).strip()
    }
    return frozenset(values)


def _family_hits(text: str, terms: Sequence[str]) -> set[str]:
    lowered = text.casefold()
    return {term for term in terms if term in lowered}


def _families(features: _MemberFeatures) -> frozenset[str]:
    hits: set[str] = set()
    for family, terms in ROLE_FAMILIES.items():
        if _family_hits(features.responsibility_text, terms):
            hits.add(family)
    return frozenset(hits)


def _member_features(snapshot: JDSnapshot) -> _MemberFeatures:
    company = next(
        (
            str(snapshot.structured_data.extensions[key]).strip()
            for key in ("company_name", "company_id", "enterprise_id")
            if snapshot.structured_data.extensions.get(key)
        ),
        None,
    )
    features = _MemberFeatures(
        jd_id=snapshot.jd_id,
        title=snapshot.title,
        company=company,
        publish_date=snapshot.publish_date,
        window_id=snapshot.window_id,
        responsibility_text=" ".join(snapshot.structured_data.responsibilities),
        skill_set=_skills(snapshot),
        families=frozenset(),
    )
    return replace(features, families=_families(features))


def _similarity_matrices(
    features: Sequence[_MemberFeatures],
    config: SplitRefinementConfig,
) -> tuple[
    list[list[float]],
    list[list[float]],
    list[list[float]],
    list[list[float]],
]:
    responsibility_vectors = _tfidf(
        [item.responsibility_text for item in features]
    )
    size = len(features)
    responsibility = [[0.0] * size for _ in range(size)]
    skills = [[0.0] * size for _ in range(size)]
    titles = [[0.0] * size for _ in range(size)]
    combined = [[0.0] * size for _ in range(size)]
    title_sets = [frozenset(_tokens(item.title)) for item in features]
    for left in range(size):
        for right in range(left + 1, size):
            resp = _cosine(
                responsibility_vectors[left], responsibility_vectors[right]
            )
            skill = _jaccard(features[left].skill_set, features[right].skill_set)
            title = _jaccard(title_sets[left], title_sets[right])
            score = (
                config.responsibility_weight * resp
                + config.skill_weight * skill
            )
            responsibility[left][right] = responsibility[right][left] = resp
            skills[left][right] = skills[right][left] = skill
            titles[left][right] = titles[right][left] = title
            combined[left][right] = combined[right][left] = score
    return responsibility, skills, titles, combined


def _pair_evidence(
    responsibility: Sequence[Sequence[float]],
    skills: Sequence[Sequence[float]],
    titles: Sequence[Sequence[float]],
    combined: Sequence[Sequence[float]],
    member_ids: Sequence[str],
) -> Mapping[str, float]:
    resp: dict[str, float] = {}
    for left in range(len(member_ids)):
        for right in range(left + 1, len(member_ids)):
            key = f"{member_ids[left]} <-> {member_ids[right]}"
            resp[key] = round(responsibility[left][right], 6)
    return resp


def _hhi(counts: Mapping[str, int]) -> float:
    total = sum(counts.values())
    if not total:
        return 0.0
    return round(
        sum((count / total) ** 2 for count in counts.values()),
        6,
    )


def _responsibility_contradiction_terms(
    features: Sequence[_MemberFeatures],
) -> int:
    counts: Counter[str] = Counter()
    for item in features:
        counts.update(item.families)
    return sum(
        1
        for family in ROLE_FAMILIES
        if 2 <= counts.get(family, 0) <= len(features) - 2
    )


def _discriminative_skills(
    features: Sequence[_MemberFeatures],
    partition: Sequence[Sequence[int]],
    config: SplitRefinementConfig,
) -> list[str]:
    skills = sorted(
        {skill for item in features for skill in item.skill_set}
    )
    result: list[str] = []
    for skill in skills:
        if skill in GENERIC_SKILLS:
            continue
        support = [
            sum(1 for index in group if skill in features[index].skill_set)
            / max(len(group), 1)
            for group in partition
        ]
        if any(value <= 0 for value in support):
            continue
        ratio = max(support) / min(support)
        if ratio >= config.skill_concentration_ratio and max(support) >= 0.5:
            result.append(skill)
    return result


def _candidate_partitions(
    features: Sequence[_MemberFeatures],
    responsibility: Sequence[Sequence[float]],
    combined: Sequence[Sequence[float]],
    config: SplitRefinementConfig,
) -> list[dict[str, Any]]:
    size = len(features)
    counts = Counter(
        family for item in features for family in item.families
    )
    candidates: list[dict[str, Any]] = []
    partitions: list[tuple[str, list[list[int]]]] = []
    for family in sorted(ROLE_FAMILIES):
        support = counts.get(family, 0)
        if config.min_subgroup_support <= support <= size - config.min_subgroup_support:
            partitions.append(
                (
                    family,
                    [
                        [
                            index
                            for index, item in enumerate(features)
                            if family in item.families
                        ],
                        [
                            index
                            for index, item in enumerate(features)
                            if family not in item.families
                        ],
                    ],
                )
            )
    company_counts = Counter(
        item.company or "unknown" for item in features
    )
    for company in sorted(company_counts):
        support = company_counts[company]
        if config.min_subgroup_support <= support <= size - config.min_subgroup_support:
            partitions.append(
                (
                    f"company:{company}",
                    [
                        [
                            index
                            for index, item in enumerate(features)
                            if (item.company or "unknown") == company
                        ],
                        [
                            index
                            for index, item in enumerate(features)
                            if (item.company or "unknown") != company
                        ],
                    ],
                )
            )
    for family, groups in partitions:
        group_coherence = [
            _mean(
                [
                    combined[left][right]
                    for left_index, left in enumerate(group)
                    for right in group[left_index + 1 :]
                ]
            )
            for group in groups
        ]
        intra: list[float] = []
        inter: list[float] = []
        resp_intra: list[float] = []
        resp_inter: list[float] = []
        for left in range(size):
            for right in range(left + 1, size):
                same = (left in groups[0]) == (right in groups[0])
                if same:
                    intra.append(combined[left][right])
                    resp_intra.append(responsibility[left][right])
                else:
                    inter.append(combined[left][right])
                    resp_inter.append(responsibility[left][right])
        discriminative = _discriminative_skills(features, groups, config)
        family_terms = set()
        for candidate_family in ROLE_FAMILIES:
            support = [
                sum(
                    1
                    for index in group
                    if candidate_family in features[index].families
                )
                / max(len(group), 1)
                for group in groups
            ]
            if max(support) >= 0.5 and min(support) <= 0.2:
                family_terms.add(candidate_family)
        if (
            family.startswith("company:")
            and config.company_partition_requires_contradiction
            and not family_terms
        ):
            continue
        candidates.append(
            {
                "family": family,
                "kind": (
                    "company" if family.startswith("company:") else "role_family"
                ),
                "groups": groups,
                "group_sizes": [len(group) for group in groups],
                "group_coherence": group_coherence,
                "intra_combined": _mean(intra),
                "inter_combined": _mean(inter),
                "intra_minus_inter": _mean(intra) - _mean(inter),
                "resp_intra": _mean(resp_intra),
                "resp_inter": _mean(resp_inter),
                "resp_separation": 1.0 - _mean(resp_inter),
                "discriminative_skills": discriminative,
                "contradiction_terms": sorted(family_terms),
            }
        )
    if config.anti_over_split_guards:
        candidates.sort(
            key=lambda item: (
                item["kind"] == "company",
                item["resp_separation"],
                item["intra_combined"],
                item["family"],
            ),
            reverse=True,
        )
    else:
        candidates.sort(
            key=lambda item: (
                item["resp_separation"],
                item["intra_combined"],
                item["family"],
            ),
            reverse=True,
        )
    return candidates


def _valid_partition(
    candidate: Mapping[str, Any],
    features: Sequence[_MemberFeatures],
    config: SplitRefinementConfig,
) -> tuple[bool, dict[str, Any]]:
    groups = candidate["groups"]
    if min(len(group) for group in groups) < config.min_subgroup_support:
        return False, {}
    if any(
        value < config.min_subgroup_combined_coherence
        for value in candidate["group_coherence"]
    ):
        return False, {}
    if candidate["resp_separation"] < config.min_responsibility_separation:
        return False, {}
    if candidate["intra_minus_inter"] < config.min_intra_minus_inter:
        return False, {}
    if len(candidate["discriminative_skills"]) < config.min_discriminative_skills:
        if len(candidate["contradiction_terms"]) < config.min_contradiction_terms:
            return False, {}
    guard_notes: list[str] = []
    if config.anti_over_split_guards:
        group_token_counts = [
            _mean([len(_tokens(features[index].responsibility_text)) for index in group])
            for group in groups
        ]
        if (
            candidate["kind"] == "company"
            and not candidate["discriminative_skills"]
            and not candidate["contradiction_terms"]
        ):
            guard_notes.append("company_not_sole_trigger")
        if (
            max(group_token_counts) < config.sparse_responsibility_min_tokens
            and not candidate["contradiction_terms"]
        ):
            guard_notes.append("sparse_responsibility")
        if guard_notes:
            return False, {"guard_notes": guard_notes}
    return True, {
        "group_coherence": candidate["group_coherence"],
        "guard_notes": guard_notes,
    }


def diagnose_cluster(
    cluster: AlgorithmCluster,
    config: SplitRefinementConfig | None = None,
) -> ClusterCoherenceDiagnostics:
    """Compute internal coherence diagnostics for one cluster."""
    config = config or SplitRefinementConfig()
    members = tuple(sorted(cluster.members, key=lambda item: item.jd_id))
    features = tuple(_member_features(item) for item in members)
    responsibility, skills, titles, combined = _similarity_matrices(
        features, config
    )
    size = len(features)
    resp_pairs = [
        responsibility[left][right]
        for left in range(size)
        for right in range(left + 1, size)
    ]
    skill_pairs = [
        skills[left][right]
        for left in range(size)
        for right in range(left + 1, size)
    ]
    title_pairs = [
        titles[left][right]
        for left in range(size)
        for right in range(left + 1, size)
    ]
    combined_pairs = [
        combined[left][right]
        for left in range(size)
        for right in range(left + 1, size)
    ]
    companies = Counter(item.company or "unknown" for item in features)
    sources = Counter(item.window_id for item in features)
    candidates = _candidate_partitions(
        features, responsibility, combined, config
    )
    discriminative = 0
    contradiction_terms = _responsibility_contradiction_terms(features)
    if candidates:
        best = candidates[0]
        discriminative = len(best["discriminative_skills"])
    titles_seen = [item.title for item in features if item.title]
    dates = [item.publish_date for item in features if item.publish_date]
    pairwise_by_id = _pair_evidence(
        responsibility, skills, titles, combined, [item.jd_id for item in features]
    )
    return ClusterCoherenceDiagnostics(
        cluster_id=cluster.key,
        member_count=size,
        title_family_coherence=round(_mean(title_pairs), 6),
        title_distinct_ratio=(
            round(len(set(titles_seen)) / len(titles_seen), 6)
            if titles_seen
            else None
        ),
        responsibility_coherence=round(_mean(resp_pairs), 6),
        skill_coherence=round(_mean(skill_pairs), 6),
        combined_coherence=round(_mean(combined_pairs), 6),
        pairwise_similarity=pairwise_by_id,
        company_distribution=dict(sorted(companies.items())),
        source_distribution=dict(sorted(sources.items())),
        company_hhi=_hhi(companies),
        responsibility_contradiction_terms=contradiction_terms,
        discriminative_skill_count=discriminative,
        candidate_subgroup_count=len(candidates),
        temporal_compatibility={
            "publish_date_min": min(dates).isoformat() if dates else None,
            "publish_date_max": max(dates).isoformat() if dates else None,
            "window_ids": sorted({item.window_id for item in features}),
            "window_count": len({item.window_id for item in features}),
            "company_count": len(companies),
            "source_platform_count": len({item.jd_id.split(":")[0] for item in features}),
        },
    )


def _child_cluster(
    parent: AlgorithmCluster,
    members: tuple[JDSnapshot, ...],
    split_record: Mapping[str, Any],
    config: SplitRefinementConfig,
) -> AlgorithmCluster:
    member_ids = sorted(item.jd_id for item in members)
    skill_counts = Counter(
        skill for item in members for skill in _skills(item)
    )
    core_skills = tuple(
        skill
        for skill, count in sorted(
            skill_counts.items(), key=lambda value: (-value[1], value[0])
        )
        if count >= math.ceil(len(members) / 2)
    )
    responsibility_counts = Counter(
        responsibility.strip()
        for item in members
        for responsibility in item.structured_data.responsibilities
        if responsibility.strip()
    )
    core_responsibilities = tuple(
        value for value, _ in responsibility_counts.most_common(5)
    )
    label = (
        " / ".join(core_skills[:3])
        or Counter(item.title for item in members).most_common(1)[0][0]
    )
    merge_basis = dict(parent.merge_basis or {})
    merge_basis["split_refinement"] = split_record
    return AlgorithmCluster(
        key=min(member_ids),
        cluster_name=f"{label} 岗位簇",
        members=members,
        core_skills=core_skills,
        stability_score=parent.stability_score,
        centroid=parent.centroid,
        algorithm_version=parent.algorithm_version,
        similarity_threshold=parent.similarity_threshold,
        random_seed=parent.random_seed,
        core_responsibilities=core_responsibilities,
        semantic_centroid=parent.semantic_centroid,
        algorithm_sources=parent.algorithm_sources,
        merge_basis=FrozenDict(merge_basis),
    )


def refine_cluster(
    cluster: AlgorithmCluster,
    config: SplitRefinementConfig | None = None,
) -> tuple[tuple[AlgorithmCluster, ...], SplitRefinementRecord | None]:
    """Return refined children (or the original cluster) plus split provenance."""
    config = config or SplitRefinementConfig()
    members = tuple(sorted(cluster.members, key=lambda item: item.jd_id))
    if len(members) < config.min_split_members:
        return (cluster,), None
    features = tuple(_member_features(item) for item in members)
    responsibility, _skills, _titles, combined = _similarity_matrices(
        features, config
    )
    size = len(features)
    combined_pairs = [
        combined[left][right]
        for left in range(size)
        for right in range(left + 1, size)
    ]
    responsibility_pairs = [
        responsibility[left][right]
        for left in range(size)
        for right in range(left + 1, size)
    ]
    if (
        _mean(combined_pairs) > config.max_average_combined_similarity
        or _mean(responsibility_pairs) > config.max_average_responsibility_similarity
    ):
        return (cluster,), None
    candidates = _candidate_partitions(
        features, responsibility, combined, config
    )
    selected = None
    selected_guard_notes: list[str] = []
    for candidate in candidates:
        valid, detail = _valid_partition(candidate, features, config)
        if valid:
            selected = candidate
            selected_guard_notes = list(detail.get("guard_notes", ()))
            break
    if selected is None:
        return (cluster,), None

    groups = selected["groups"]
    child_ids = [
        min(features[index].jd_id for index in group) for group in groups
    ]
    assignment = {
        child_id: tuple(sorted(features[index].jd_id for index in group))
        for child_id, group in zip(child_ids, groups, strict=True)
    }
    group_coherence = []
    for group in groups:
        pair_values = [
            combined[left][right]
            for left_index, left in enumerate(group)
            for right in group[left_index + 1 :]
        ]
        group_coherence.append(round(_mean(pair_values), 6))
    confidence = round(
        min(
            1.0,
            0.5 * selected["resp_separation"]
            + 0.3 * min(group_coherence)
            + 0.2 * min(1.0, len(selected["discriminative_skills"]) / 2.0),
        ),
        6,
    )
    record = SplitRefinementRecord(
        parent_cluster_id=cluster.key,
        child_cluster_ids=tuple(child_ids),
        member_assignment=assignment,
        split_trigger=(
            "internal_heterogeneity",
            "two_coherent_subgroups",
            "responsibility_skill_contradiction",
            "minimum_support_satisfied",
            *(
                tuple(selected_guard_notes)
                if selected_guard_notes
                else ("anti_over_split_guards_passed",)
            ),
        ),
        title_evidence={
            "parent_titles": sorted({item.title for item in members}),
            "title_used_for_split": False,
            "title_family_note": (
                "titles are diagnostics only; splits require responsibility evidence"
            ),
        },
        responsibility_evidence={
            "split_family": selected["family"],
            "responsibility_separation": round(
                selected["resp_separation"], 6
            ),
            "responsibility_intra_group_mean": round(
                selected["resp_intra"], 6
            ),
            "responsibility_inter_group_mean": round(
                selected["resp_inter"], 6
            ),
            "contradiction_terms": selected["contradiction_terms"],
        },
        skill_evidence={
            "discriminative_skills": selected["discriminative_skills"],
            "generic_skills_excluded": sorted(GENERIC_SKILLS),
        },
        subgroup_coherence={
            "child_cluster_ids": list(child_ids),
            "combined_coherence_by_child": dict(
                zip(child_ids, group_coherence, strict=True)
            ),
            "support_by_child": dict(
                zip(child_ids, [len(group) for group in groups], strict=True)
            ),
        },
        inter_group_contradiction={
            "inter_group_combined_mean": round(
                selected["inter_combined"], 6
            ),
            "responsibility_separation": round(
                selected["resp_separation"], 6
            ),
            "discriminative_skill_count": len(
                selected["discriminative_skills"]
            ),
            "contradiction_term_count": len(
                selected["contradiction_terms"]
            ),
            "guard_notes": selected_guard_notes,
        },
        confidence=confidence,
        algorithm_version=config.algorithm_version,
    )
    children = tuple(
        _child_cluster(
            cluster,
            tuple(members[index] for index in group),
            FrozenDict(record.to_json()),
            config,
        )
        for group in groups
    )
    return children, record


def refine_clusters(
    clusters: Iterable[AlgorithmCluster],
    config: SplitRefinementConfig | None = None,
) -> RefinementOutcome:
    """Apply the deterministic split refinement to a clustering result."""
    config = config or SplitRefinementConfig()
    refined: list[AlgorithmCluster] = []
    diagnostics: list[ClusterCoherenceDiagnostics] = []
    splits: list[SplitRefinementRecord] = []
    for cluster in sorted(clusters, key=lambda item: item.key):
        children, record = refine_cluster(cluster, config)
        diagnostics.append(diagnose_cluster(cluster, config))
        refined.extend(children)
        if record is not None:
            splits.append(record)
    return RefinementOutcome(
        clusters=tuple(refined),
        diagnostics=tuple(diagnostics),
        splits=tuple(splits),
    )
