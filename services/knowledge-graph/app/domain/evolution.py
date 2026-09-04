"""Explainable position capability evolution events over graph version snapshots.

Events are pure read-model derivations.  They never mutate graph facts or
published versions.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone


DEFAULT_EVOLUTION_CONFIG: dict[str, object] = {
    "detector_version": "position-evolution-events-v5",
    "config_version": "evolution-defaults-v1",
    "emergence_min_absolute_weight": 0.20,
    "emergence_min_delta": 0.12,
    "decline_min_delta": 0.12,
    "significance_min_weight": 0.10,
    "replacement_min_confidence": 0.55,
    "stack_min_related_replacements": 2,
    "responsibility_similarity_threshold": 0.60,
    "responsibility_min_changed": 2,
    "responsibility_min_overlap": 0.05,
    "role_expansion_threshold": 0.25,
    "role_contraction_threshold": 0.25,
    "split_min_category_skills_removed": 3,
    "split_min_responsibilities_removed": 2,
    "split_min_skill_fraction": 0.25,
    "merge_min_category_skills_added": 3,
    "merge_min_responsibilities_added": 2,
    "merge_min_skill_fraction": 0.25,
    "noise_min_absolute_weight_change": 0.03,
    # — coverage comparability gates（降低 coverage/采样波动导致的误检）—
    "min_comparable_samples": 3,
    "min_support_documents": 3,
    # 单源技能的消失/出现视为采样波动：只有唯一来源抓到该技能时，
    # 其"消失"很可能是该来源本轮未覆盖，而非真实市场衰退。
    "min_source_diversity": 2,
    "coverage_artifact": {
        "min_weight_delta": 0.05,
        "min_source_delta": 1,
        "min_enterprise_delta": 1,
        "min_sample_delta": 3,
        "coverage_explanation_ratio": 0.5,
    },
    # — A19: seniority / credential threshold relaxation events —
    "seniority_inflation": {
        "min_years_drop": 1.0,
        "min_modality_shift": 1,
    },
    "credential_relaxation": {
        "degree_rank": [
            "high_school",
            "associate",
            "bachelor",
            "master",
            "doctorate",
        ],
        "min_degree_rank_drop": 1,
        "min_certificate_drop_count": 1,
    },
    "confidence": {
        "signal_strength": 0.45,
        "cross_metric_consistency": 0.25,
        "evidence_quality": 0.15,
        "semantic_consistency": 0.15,
    },
}

EVENT_TYPES = frozenset(
    {
        "skill_emergence",
        "skill_decline",
        "skill_replacement",
        "technology_stack_migration",
        "responsibility_shift",
        "role_expansion",
        "role_contraction",
        "position_rename",
        "position_split",
        "position_merge",
        "coverage_artifact",
        "seniority_inflation",
        "credential_relaxation",
    }
)

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _bounded(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _merged_config(config: Mapping[str, object] | None) -> dict[str, object]:
    merged = {**DEFAULT_EVOLUTION_CONFIG, **(config or {})}
    weights = dict(merged["confidence"])
    if any(float(value) < 0 for value in weights.values()):
        raise ValueError("evolution confidence weights must be non-negative")
    if abs(sum(float(value) for value in weights.values()) - 1.0) > 1e-9:
        raise ValueError("evolution confidence weights must sum to one")
    return merged


def _replay_context(config: Mapping[str, object]) -> dict[str, object] | None:
    """Extract controlled-replay identity keys from config, if present."""
    replay: dict[str, object] = {}
    for key in (
        "_replay_catalog_snapshot_id",
        "_replay_catalog_source_version",
        "_replay_validation_policy_version",
        "_replay_mapping_policy_version",
        "_replay_aggregation_algorithm_version",
        "_replay_normalized_config",
        "_replay_human_review_frozen",
    ):
        value = config.get(key)
        if value is not None:
            replay[key] = value
    return replay if replay else None


def _normalize_snapshot(snapshot: Mapping[str, object]) -> dict:
    current = deepcopy(dict(snapshot))
    if "skill_relations" not in current:
        current["skill_relations"] = deepcopy(list(current.get("skills", [])))
    if "responsibilities" not in current:
        current["responsibilities"] = deepcopy(list(current.get("task_profile", [])))
    current.pop("skills", None)
    current.pop("task_profile", None)
    return current


def _weight(item: Mapping[str, object]) -> float:
    for key in ("final_weight", "weight", "auto_weight"):
        value = item.get(key)
        if value is not None:
            return float(value)
    return 0.0


CONFIDENCE_UNAVAILABLE = 0.20

EVIDENCE_QUALITY_UNAVAILABLE = 0.25


def _confidence_value(item: Mapping[str, object]) -> float:
    for key in ("final_confidence", "confidence", "auto_confidence"):
        value = item.get(key)
        if value is not None:
            return float(value)
    return CONFIDENCE_UNAVAILABLE


def _read_relation_metric(item: Mapping[str, object], key: str) -> float | None:
    for field in ("statistics", "metrics"):
        container = item.get(field)
        if isinstance(container, Mapping) and key in container:
            try:
                return float(container[key])
            except (TypeError, ValueError):
                continue
    return None


def _support_documents(item: Mapping[str, object] | None) -> int | None:
    if item is None:
        return None
    value = _read_relation_metric(item, "support_document_count")
    return int(value) if value is not None else None


def _snapshot_sample_count(snapshot: Mapping[str, object]) -> int:
    """读取快照级样本量；缺失时退化为关系 support_document_count 的最大值。"""
    sample_stats = snapshot.get("sample_stats") or {}
    value = sample_stats.get("included_samples")
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    best = 0
    for item in snapshot.get("skill_relations", []):
        if not isinstance(item, Mapping):
            continue
        support = _read_relation_metric(item, "support_document_count")
        if support is not None:
            best = max(best, int(support))
    return best


def _coverage_change_count(
    before_item: Mapping[str, object],
    after_item: Mapping[str, object],
    config: Mapping[str, object],
) -> int:
    ca_cfg = dict(config.get("coverage_artifact", {}))
    before_stats = before_item.get("statistics") or {}
    after_stats = after_item.get("statistics") or {}
    if not isinstance(before_stats, Mapping) or not isinstance(after_stats, Mapping):
        return 0
    src_delta = abs(
        int(after_stats.get("source_diversity", 0))
        - int(before_stats.get("source_diversity", 0))
    )
    ent_delta = abs(
        int(after_stats.get("enterprise_coverage", 0))
        - int(before_stats.get("enterprise_coverage", 0))
    )
    smp_delta = abs(
        int(after_stats.get("support_document_count", 0))
        - int(before_stats.get("support_document_count", 0))
    )
    return sum([
        1 if src_delta >= int(ca_cfg.get("min_source_delta", 1)) else 0,
        1 if ent_delta >= int(ca_cfg.get("min_enterprise_delta", 1)) else 0,
        1 if smp_delta >= int(ca_cfg.get("min_sample_delta", 3)) else 0,
    ])


def _is_coverage_driven(
    before_item: Mapping[str, object] | None,
    after_item: Mapping[str, object] | None,
    delta: float,
    config: Mapping[str, object],
    before_sample_count: int,
    after_sample_count: int,
) -> bool:
    """判定某技能信号是否由覆盖/采样波动驱动（而非真实结构演化）。

    三种情形：
    1. before 缺该技能（emergence）：before 版本样本量不足，或 after 技能支持文档过少；
    2. after 缺该技能（decline）：after 版本样本量不足，或 before 技能支持文档过少，
       或 before 技能为单源（source_diversity 低于阈值——唯一来源未覆盖不等于衰退）；
    3. 两版本都在（权重变化）：任一版本支持文档过少（采样波动），或
       ≥2/3 覆盖指标同时显著变化（同 A18 coverage_artifact 判定）。
    """
    min_comparable = int(config.get("min_comparable_samples", 3))
    min_support = int(config.get("min_support_documents", 3))
    min_source = int(config.get("min_source_diversity", 2))

    def weak(item: Mapping[str, object] | None) -> bool:
        support = _support_documents(item)
        return support is not None and support < min_support

    def single_source(item: Mapping[str, object] | None) -> bool:
        if item is None:
            return False
        src_div = _read_relation_metric(item, "source_diversity")
        return src_div is not None and src_div < min_source

    if before_item is None:
        return (
            before_sample_count < min_comparable
            or weak(after_item)
            or single_source(after_item)
        )
    if after_item is None:
        return (
            after_sample_count < min_comparable
            or weak(before_item)
            or single_source(before_item)
        )
    if weak(before_item) or weak(after_item):
        return True
    # 单源技能在两版本都存在、但只有唯一来源覆盖时，其权重变化不可靠，
    # 属采样波动（同一来源 JD 数量波动即可改变权重），不判真实演化。
    if single_source(before_item) and single_source(after_item):
        return True
    ca_cfg = dict(config.get("coverage_artifact", {}))
    if abs(delta) < float(ca_cfg.get("min_weight_delta", 0.05)):
        return False
    return _coverage_change_count(before_item, after_item, config) >= 2


def _evidence_quality(item: Mapping[str, object]) -> float:
    support_docs = _read_relation_metric(item, "support_document_count")
    source_div = _read_relation_metric(item, "source_diversity")
    enterprise_cov = _read_relation_metric(item, "enterprise_coverage")

    available = [v for v in (support_docs, source_div, enterprise_cov) if v is not None]
    if not available:
        return EVIDENCE_QUALITY_UNAVAILABLE

    support_score = min(1.0, (support_docs or 0) / 5.0)
    diversity_score = min(1.0, (source_div or 0) / 3.0)
    coverage_score = min(1.0, (enterprise_cov or 0) / 3.0)
    raw = (support_score + diversity_score + coverage_score) / 3.0
    return round(_bounded(0.3 + 0.7 * raw), 6)


def _evidence_quality_details(item: Mapping[str, object]) -> dict[str, object]:
    support_docs = _read_relation_metric(item, "support_document_count")
    source_div = _read_relation_metric(item, "source_diversity")
    enterprise_cov = _read_relation_metric(item, "enterprise_coverage")

    available = [v for v in (support_docs, source_div, enterprise_cov) if v is not None]
    quality = (
        EVIDENCE_QUALITY_UNAVAILABLE if not available
        else _evidence_quality(item)
    )
    return {
        "quality": quality,
        "available": len(available) > 0,
        "components": {
            "support_document_count": support_docs,
            "source_diversity": source_div,
            "enterprise_coverage": enterprise_cov,
        },
        "reason": (
            "EVIDENCE_QUALITY_UNAVAILABLE: no statistics or metrics found on relation"
            if not available else
            f"computed from {len(available)}/3 evidence dimensions"
        ),
    }


def _category_codes(item: Mapping[str, object]) -> tuple[str, ...]:
    codes: list[str] = []
    category = item.get("category_code")
    if category:
        codes.append(str(category))
    subcategory = item.get("subcategory_code")
    if subcategory:
        codes.append(str(subcategory))
    for classification in item.get("classifications") or []:
        code = classification.get("code") if isinstance(classification, Mapping) else None
        if code:
            codes.append(str(code))
    return tuple(sorted(set(codes)))


def _semantic_context(item: Mapping[str, object]) -> str:
    codes = _category_codes(item)
    return codes[0] if codes else "unknown"


def _related_context(
    left: Mapping[str, object], right: Mapping[str, object]
) -> bool:
    left_codes = set(_category_codes(left))
    right_codes = set(_category_codes(right))
    return bool(left_codes & right_codes)


def _responsibility_text(item: object) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, Mapping):
        return str(item)
    payload = item.get("payload")
    if isinstance(payload, Mapping):
        for key in ("text", "content", "name", "title"):
            value = payload.get(key)
            if value:
                return str(value)
    for key in ("text", "content", "name", "requirement_text", "title"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


_RESPONSIBILITY_NOISE_MARKERS = (
    # 平台/抓取痕迹
    "boss", "直聘", "kanzhun", "liepin", "猎聘",
    "安全提示", "举报", "投诉", "扫码", "分享",
    # 法律/资质/版权
    "版权", "copyright", "icp", "京公网安备", "营业执照", "许可证",
    "个人信息保护", "用户服务协议", "企业服务热线", "未成年人",
    # 公司元数据
    "公司名称", "成立日期", "企业类型", "注册资金",
    # 页面/产品 chrome
    "个人综合排名", "个人竞争力", "登录查看", "查看完整",
)


def _is_responsibility_noise(text: str) -> bool:
    """识别 JD 抓取噪声（平台提示、版权、公司元数据、产品 chrome 等非职责文本）。"""
    lowered = text.casefold()
    return any(marker in lowered for marker in _RESPONSIBILITY_NOISE_MARKERS)


def _responsibility_set(snapshot: Mapping[str, object]) -> frozenset[str]:
    values: set[str] = set()
    for item in snapshot.get("responsibilities") or []:
        text = " ".join(_responsibility_text(item).casefold().split())
        if text and not _is_responsibility_noise(text):
            values.add(text)
    for item in snapshot.get("requirement_profile") or []:
        if not isinstance(item, Mapping) or item.get("kind") in ("skill", "education"):
            continue
        text = " ".join(_responsibility_text(item).casefold().split())
        if text and not _is_responsibility_noise(text):
            values.add(text)
    return frozenset(values)


def _position_name(snapshot: Mapping[str, object]) -> str:
    position = snapshot.get("position") or {}
    return str(position.get("name") or snapshot.get("position_id") or "")


def _confidence(
    signal_strength: float,
    cross_metric_consistency: float,
    evidence_quality: float,
    semantic_consistency: float,
    config: Mapping[str, object],
) -> float:
    weights = config["confidence"]
    return round(
        _bounded(
            signal_strength * float(weights["signal_strength"])
            + cross_metric_consistency * float(weights["cross_metric_consistency"])
            + evidence_quality * float(weights["evidence_quality"])
            + semantic_consistency * float(weights["semantic_consistency"])
        ),
        6,
    )


@dataclass(frozen=True)
class AtomicSkillSignal:
    kind: str
    skill_id: str
    relation: Mapping[str, object]
    before_weight: float | None
    after_weight: float | None
    delta: float
    confidence: float
    evidence_quality: float
    evidence_available: bool
    confidence_available: bool
    semantic_context: str
    coverage_driven: bool = False


def _atomic_skill_signals(
    before: Mapping[str, object],
    after: Mapping[str, object],
    config: Mapping[str, object],
) -> list[AtomicSkillSignal]:
    before_relations = {
        str(item["skill_id"]): item
        for item in before.get("skill_relations", [])
        if isinstance(item, Mapping) and item.get("skill_id")
    }
    after_relations = {
        str(item["skill_id"]): item
        for item in after.get("skill_relations", [])
        if isinstance(item, Mapping) and item.get("skill_id")
    }
    def _has_real_evidence(item: Mapping[str, object] | None) -> bool:
        if item is None:
            return False
        for field in ("statistics", "metrics"):
            container = item.get(field)
            if isinstance(container, Mapping) and len(container) > 0:
                return True
        return False

    def _has_real_confidence(item: Mapping[str, object] | None) -> bool:
        if item is None:
            return False
        for key in ("final_confidence", "confidence", "auto_confidence"):
            if item.get(key) is not None:
                return True
        return False

    signals: list[AtomicSkillSignal] = []
    before_sample_count = _snapshot_sample_count(before)
    after_sample_count = _snapshot_sample_count(after)
    for skill_id in sorted(before_relations.keys() | after_relations.keys()):
        before_item = before_relations.get(skill_id)
        after_item = after_relations.get(skill_id)
        before_weight = _weight(before_item) if before_item is not None else None
        after_weight = _weight(after_item) if after_item is not None else None
        active_item = after_item if after_item is not None else before_item
        evidence_quality = (
            _evidence_quality(active_item)
            if active_item is not None
            else EVIDENCE_QUALITY_UNAVAILABLE
        )
        evidence_available = _has_real_evidence(active_item)
        confidence_available = _has_real_confidence(active_item)
        if before_item is None:
            if after_weight >= float(config["emergence_min_absolute_weight"]):
                strength = _bounded(
                    after_weight
                    / max(float(config["emergence_min_absolute_weight"]), 0.01)
                )
                signals.append(
                    AtomicSkillSignal(
                        kind="skill_emergence",
                        skill_id=skill_id,
                        relation=after_item,
                        before_weight=None,
                        after_weight=after_weight,
                        delta=after_weight,
                        confidence=_confidence(
                            strength, 0.8, evidence_quality, 1.0, config
                        ),
                        evidence_quality=evidence_quality,
                        evidence_available=evidence_available,
                        confidence_available=confidence_available,
                        semantic_context=_semantic_context(after_item),
                        coverage_driven=_is_coverage_driven(
                            None, after_item, after_weight, config,
                            before_sample_count, after_sample_count,
                        ),
                    )
                )
            continue
        if after_item is None:
            if before_weight >= float(config["significance_min_weight"]):
                strength = _bounded(
                    before_weight
                    / max(float(config["significance_min_weight"]), 0.01)
                )
                signals.append(
                    AtomicSkillSignal(
                        kind="skill_decline",
                        skill_id=skill_id,
                        relation=before_item,
                        before_weight=before_weight,
                        after_weight=None,
                        delta=-before_weight,
                        confidence=_confidence(
                            strength, 0.8, evidence_quality, 1.0, config
                        ),
                        evidence_quality=evidence_quality,
                        evidence_available=evidence_available,
                        confidence_available=confidence_available,
                        semantic_context=_semantic_context(before_item),
                        coverage_driven=_is_coverage_driven(
                            before_item, None, -before_weight, config,
                            before_sample_count, after_sample_count,
                        ),
                    )
                )
            continue
        delta = after_weight - before_weight
        if (
            delta >= float(config["emergence_min_delta"])
            and after_weight >= float(config["significance_min_weight"])
        ):
            strength = _bounded(
                delta / max(float(config["emergence_min_delta"]), 0.01)
            )
            cross = 0.9 if _confidence_value(after_item) >= 0.6 else 0.7
            signals.append(
                AtomicSkillSignal(
                    kind="skill_emergence",
                    skill_id=skill_id,
                    relation=after_item,
                    before_weight=before_weight,
                    after_weight=after_weight,
                    delta=delta,
                    confidence=_confidence(
                        strength, cross, evidence_quality, 1.0, config
                    ),
                    evidence_quality=evidence_quality,
                    evidence_available=evidence_available,
                    confidence_available=confidence_available,
                    semantic_context=_semantic_context(after_item),
                    coverage_driven=_is_coverage_driven(
                        before_item, after_item, delta, config,
                        before_sample_count, after_sample_count,
                    ),
                )
            )
        elif (
            delta <= -float(config["decline_min_delta"])
            and before_weight >= float(config["significance_min_weight"])
        ):
            strength = _bounded(
                abs(delta) / max(float(config["decline_min_delta"]), 0.01)
            )
            cross = 0.9 if _confidence_value(before_item) >= 0.6 else 0.7
            signals.append(
                AtomicSkillSignal(
                    kind="skill_decline",
                    skill_id=skill_id,
                    relation=before_item,
                    before_weight=before_weight,
                    after_weight=after_weight,
                    delta=delta,
                    confidence=_confidence(
                        strength, cross, evidence_quality, 1.0, config
                    ),
                    evidence_quality=evidence_quality,
                    evidence_available=evidence_available,
                    confidence_available=confidence_available,
                    semantic_context=_semantic_context(before_item),
                    coverage_driven=_is_coverage_driven(
                        before_item, after_item, delta, config,
                        before_sample_count, after_sample_count,
                    ),
                )
            )
    return signals


def _relation_payload(signal: AtomicSkillSignal) -> dict:
    return deepcopy(dict(signal.relation))


def _event(
    *,
    event_type: str,
    position_id: str,
    from_version_id: int,
    to_version_id: int,
    source_entities: list[object],
    target_entities: list[object],
    confidence: float,
    magnitude: float,
    evidence: Mapping[str, object],
    reason: str,
    detector_version: str,
    config_version: str,
    created_at: datetime,
    metadata: Mapping[str, object] | None = None,
    metrics: Mapping[str, object] | None = None,
    confidence_available: bool = True,
    confidence_source: str = "computed",
    replay_context: Mapping[str, object] | None = None,
) -> dict:
    event_dict: dict[str, object] = {
        "event_id": "",
        "event_type": event_type,
        "position_id": position_id,
        "from_version": from_version_id,
        "to_version": to_version_id,
        "from_graph_version_id": from_version_id,
        "to_graph_version_id": to_version_id,
        "source_entities": source_entities,
        "target_entities": target_entities,
        "confidence": round(_bounded(confidence), 6),
        "confidence_available": confidence_available,
        "confidence_source": confidence_source,
        "magnitude": round(_bounded(magnitude), 6),
        "evidence": dict(evidence),
        "reason": reason,
        "detector_version": detector_version,
        "config_version": config_version,
        "created_at": created_at.isoformat(),
        "metrics": dict(metrics or {}),
        "metadata": dict(metadata or {}),
    }
    if replay_context is not None:
        event_dict["replay_context"] = dict(replay_context)
    return event_dict


# —— Degree rank lookup for credential_relaxation ——
_DEGREE_RANK: dict[str, int] = {
    "high_school": 0,
    "associate": 1,
    "bachelor": 2,
    "master": 3,
    "doctorate": 4,
}


def _parse_degree_rank(degree_text: str | None) -> int | None:
    if degree_text is None:
        return None
    text = degree_text.strip().lower()
    # 支持中英文常见学历表述
    for keyword, rank in [
        ("doctorate", 4), ("phd", 4), ("博士", 4),
        ("master", 3), ("硕士", 3), ("mba", 3),
        ("bachelor", 2), ("本科", 2), ("学士", 2),
        ("associate", 1), ("大专", 1), ("专科", 1),
        ("high_school", 0), ("高中", 0),
    ]:
        if keyword in text:
            return rank
    return None


def _parse_req_years(item: Mapping[str, object]) -> float | None:
    """从 requirement 条目中提取经验年数。"""
    for key in ("minimum_years", "min_years", "years", "experience_years"):
        val = item.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def _parse_req_degree_rank(item: Mapping[str, object]) -> int | None:
    """从 requirement 条目中提取学历等级。"""
    for key in ("minimum_degree", "degree", "education_level"):
        val = item.get(key)
        if isinstance(val, str):
            rank = _parse_degree_rank(val)
            if rank is not None:
                return rank
    return None


def _parse_req_certificates(item: Mapping[str, object]) -> frozenset[str]:
    """从 requirement 条目中提取证书集合。"""
    certs = item.get("certificates")
    if isinstance(certs, list):
        return frozenset(str(c) for c in certs if c)
    cert = item.get("certificate")
    if isinstance(cert, str) and cert:
        return frozenset({cert})
    return frozenset()


def _seniority_inflation_events(
    before: Mapping[str, object],
    after: Mapping[str, object],
    position_id: str,
    from_version_id: int,
    to_version_id: int,
    lineage: Mapping[str, object],
    detector_version: str,
    config_version: str,
    created_at: datetime,
    config: Mapping[str, object],
    replay_context: Mapping[str, object] | None = None,
) -> list[dict]:
    """检测经验年限要求降低（seniority_inflation）。

    当同一岗位的 experience 类 requirement 的 minimum_years
    在 before→after 之间显著下降（>=1年）时触发。
    """
    si_cfg = dict(config.get("seniority_inflation", {}))
    min_years_drop = float(si_cfg.get("min_years_drop", 1.0))

    before_exp = [
        item for item in before.get("requirement_profile", [])
        if isinstance(item, Mapping) and str(item.get("kind", "")).lower() == "experience"
    ]
    after_exp = [
        item for item in after.get("requirement_profile", [])
        if isinstance(item, Mapping) and str(item.get("kind", "")).lower() == "experience"
    ]

    events: list[dict] = []
    before_years: dict[str, float] = {}
    for item in before_exp:
        rid = str(item.get("requirement_id", item.get("aggregate_id", "")))
        years = _parse_req_years(item)
        if rid and years is not None:
            before_years[rid] = years

    after_years: dict[str, float] = {}
    for item in after_exp:
        rid = str(item.get("requirement_id", item.get("aggregate_id", "")))
        years = _parse_req_years(item)
        if rid and years is not None:
            after_years[rid] = years

    # 按 requirement_id 匹配比较
    for rid, before_y in sorted(before_years.items()):
        after_y = after_years.get(rid)
        if after_y is not None and (before_y - after_y) >= min_years_drop:
            before_item = next(
                (i for i in before_exp
                 if str(i.get("requirement_id", i.get("aggregate_id", ""))) == rid),
                None,
            )
            after_item_parsed = next(
                (i for i in after_exp
                 if str(i.get("requirement_id", i.get("aggregate_id", ""))) == rid),
                None,
            )
            magnitude = min(1.0, (before_y - after_y) / 5.0)
            # 检查 before/after 条目中是否有 evidence
            before_ev = (
                dict(before_item.get("evidence", {}))
                if isinstance(before_item, Mapping) and before_item.get("evidence")
                else {}
            )
            after_ev = (
                dict(after_item_parsed.get("evidence", {}))
                if isinstance(after_item_parsed, Mapping) and after_item_parsed.get("evidence")
                else {}
            )
            has_evidence = bool(before_ev or after_ev)
            events.append(
                _event(
                    event_type="seniority_inflation",
                    position_id=position_id,
                    from_version_id=from_version_id,
                    to_version_id=to_version_id,
                    source_entities=[{"requirement_id": rid, "minimum_years": before_y}],
                    target_entities=[{"requirement_id": rid, "minimum_years": after_y}],
                    confidence=_bounded(magnitude * 0.85),
                    magnitude=magnitude,
                    evidence={
                        "lineage": lineage,
                        "source_relations": [
                            {"requirement_id": rid, "minimum_years": before_y}
                        ],
                        "target_relations": [
                            {"requirement_id": rid, "minimum_years": after_y}
                        ],
                        "before_evidence": before_ev,
                        "after_evidence": after_ev,
                        "source": "requirement_profile_experience_comparison",
                    },
                    reason=(
                        f"experience requirement {rid}: "
                        f"minimum_years dropped from {before_y} to {after_y} "
                        f"(Δ={round(before_y - after_y, 1)})"
                    ),
                    detector_version=detector_version,
                    config_version=config_version,
                    created_at=created_at,
                    metadata={
                        "atomic_signals": ["experience_years_decrease"],
                        "seniority_inflation": {
                            "requirement_id": rid,
                            "before_years": before_y,
                            "after_years": after_y,
                            "years_drop": round(before_y - after_y, 1),
                        },
                    },
                    metrics={
                        "before_minimum_years": before_y,
                        "after_minimum_years": after_y,
                        "years_drop": round(before_y - after_y, 1),
                    },
                    confidence_available=has_evidence,
                    confidence_source=(
                        "computed" if has_evidence
                        else "EVIDENCE_QUALITY_UNAVAILABLE"
                    ),
                    replay_context=replay_context,
                )
            )

    # 如果 requirement_id 不匹配，比较聚合值（before 经验要求整体 vs after）
    if not events:
        before_avg = (
            sum(before_years.values()) / len(before_years)
            if before_years else None
        )
        after_avg = (
            sum(after_years.values()) / len(after_years)
            if after_years else None
        )
        if (
            before_avg is not None and after_avg is not None
            and (before_avg - after_avg) >= min_years_drop
        ):
            events.append(
                _event(
                    event_type="seniority_inflation",
                    position_id=position_id,
                    from_version_id=from_version_id,
                    to_version_id=to_version_id,
                    source_entities=[
                        {"aggregate": "experience_requirements",
                         "avg_minimum_years": round(before_avg, 1)}
                    ],
                    target_entities=[
                        {"aggregate": "experience_requirements",
                         "avg_minimum_years": round(after_avg, 1)}
                    ],
                    confidence=_bounded(
                        ((before_avg - after_avg) / 5.0) * 0.7
                    ),
                    magnitude=min(1.0, (before_avg - after_avg) / 5.0),
                    evidence={
                        "lineage": lineage,
                        "source_relations": [
                            {"avg_minimum_years": round(before_avg, 1),
                             "requirement_count": len(before_years)}
                        ],
                        "target_relations": [
                            {"avg_minimum_years": round(after_avg, 1),
                             "requirement_count": len(after_years)}
                        ],
                        "source": "requirement_profile_aggregate_experience_comparison",
                    },
                    reason=(
                        f"aggregate experience requirements: "
                        f"avg minimum_years dropped from {round(before_avg, 1)} "
                        f"to {round(after_avg, 1)} "
                        f"(Δ={round(before_avg - after_avg, 1)})"
                    ),
                    detector_version=detector_version,
                    config_version=config_version,
                    created_at=created_at,
                    metadata={
                        "atomic_signals": ["experience_years_aggregate_decrease"],
                        "seniority_inflation": {
                            "before_avg_years": round(before_avg, 1),
                            "after_avg_years": round(after_avg, 1),
                            "years_drop": round(before_avg - after_avg, 1),
                            "aggregation": "mean",
                        },
                    },
                    metrics={
                        "before_avg_minimum_years": round(before_avg, 1),
                        "after_avg_minimum_years": round(after_avg, 1),
                        "before_requirement_count": len(before_years),
                        "after_requirement_count": len(after_years),
                    },
                    confidence_available=False,
                    confidence_source="EVIDENCE_QUALITY_UNAVAILABLE;AGGREGATE_ESTIMATE",
                    replay_context=replay_context,
                )
            )

    return events


def _credential_relaxation_events(
    before: Mapping[str, object],
    after: Mapping[str, object],
    position_id: str,
    from_version_id: int,
    to_version_id: int,
    lineage: Mapping[str, object],
    detector_version: str,
    config_version: str,
    created_at: datetime,
    config: Mapping[str, object],
    replay_context: Mapping[str, object] | None = None,
) -> list[dict]:
    """检测学历/证书要求放宽（credential_relaxation）。

    涵盖两类变化：
    1. education 类的 minimum_degree 降级（如 master→bachelor）
    2. certificate 类的证书数量/严格度减少
    """
    cr_cfg = dict(config.get("credential_relaxation", {}))
    min_degree_drop = int(cr_cfg.get("min_degree_rank_drop", 1))
    min_cert_drop = int(cr_cfg.get("min_certificate_drop_count", 1))
    degree_rank_list: list[str] = list(cr_cfg.get("degree_rank", _DEGREE_RANK.keys()))

    before_reqs = [
        item for item in before.get("requirement_profile", [])
        if isinstance(item, Mapping)
    ]
    after_reqs = [
        item for item in after.get("requirement_profile", [])
        if isinstance(item, Mapping)
    ]

    events: list[dict] = []

    # —— education: minimum_degree 降级 ——
    before_edu = [
        item for item in before_reqs
        if str(item.get("kind", "")).lower() == "education"
    ]
    after_edu = [
        item for item in after_reqs
        if str(item.get("kind", "")).lower() == "education"
    ]

    for before_item in before_edu:
        rid = str(before_item.get(
            "requirement_id", before_item.get("aggregate_id", "")
        ))
        before_rank = _parse_req_degree_rank(before_item)
        if before_rank is None:
            continue

        after_match = next(
            (item for item in after_edu
             if str(item.get("requirement_id", item.get("aggregate_id", ""))) == rid),
            None,
        )
        if after_match is None:
            continue

        after_rank = _parse_req_degree_rank(after_match)
        if after_rank is not None and (before_rank - after_rank) >= min_degree_drop:
            before_deg = degree_rank_list[before_rank] if before_rank < len(degree_rank_list) else str(before_rank)
            after_deg = degree_rank_list[after_rank] if after_rank < len(degree_rank_list) else str(after_rank)
            before_ev = (
                dict(before_item.get("evidence", {}))
                if isinstance(before_item, Mapping) and before_item.get("evidence")
                else {}
            )
            after_ev = (
                dict(after_match.get("evidence", {}))
                if isinstance(after_match, Mapping) and after_match.get("evidence")
                else {}
            )
            has_evidence = bool(before_ev or after_ev)
            events.append(
                _event(
                    event_type="credential_relaxation",
                    position_id=position_id,
                    from_version_id=from_version_id,
                    to_version_id=to_version_id,
                    source_entities=[
                        {"requirement_id": rid, "kind": "education",
                         "minimum_degree": before_deg}
                    ],
                    target_entities=[
                        {"requirement_id": rid, "kind": "education",
                         "minimum_degree": after_deg}
                    ],
                    confidence=_bounded(
                        (before_rank - after_rank) / 3.0 * 0.80
                    ),
                    magnitude=min(1.0, (before_rank - after_rank) / 3.0),
                    evidence={
                        "lineage": lineage,
                        "source_relations": [
                            {"requirement_id": rid, "minimum_degree": before_deg}
                        ],
                        "target_relations": [
                            {"requirement_id": rid, "minimum_degree": after_deg}
                        ],
                        "before_evidence": before_ev,
                        "after_evidence": after_ev,
                        "source": "requirement_profile_education_comparison",
                    },
                    reason=(
                        f"education requirement {rid}: "
                        f"minimum_degree relaxed from {before_deg} to {after_deg}"
                    ),
                    detector_version=detector_version,
                    config_version=config_version,
                    created_at=created_at,
                    metadata={
                        "atomic_signals": ["degree_requirement_relaxation"],
                        "credential_relaxation": {
                            "requirement_id": rid,
                            "before_degree": before_deg,
                            "after_degree": after_deg,
                            "degree_rank_drop": before_rank - after_rank,
                        },
                    },
                    metrics={
                        "before_degree_rank": before_rank,
                        "after_degree_rank": after_rank,
                        "degree_rank_drop": before_rank - after_rank,
                    },
                    confidence_available=has_evidence,
                    confidence_source=(
                        "computed" if has_evidence
                        else "EVIDENCE_QUALITY_UNAVAILABLE"
                    ),
                    replay_context=replay_context,
                )
            )

    # —— certificate: 证书数量减少 ——
    before_cert = [
        item for item in before_reqs
        if str(item.get("kind", "")).lower() == "certificate"
    ]
    after_cert = [
        item for item in after_reqs
        if str(item.get("kind", "")).lower() == "certificate"
    ]

    before_cert_set: frozenset[str] = frozenset()
    for item in before_cert:
        before_cert_set |= _parse_req_certificates(item)
    after_cert_set: frozenset[str] = frozenset()
    for item in after_cert:
        after_cert_set |= _parse_req_certificates(item)

    dropped_certs = before_cert_set - after_cert_set
    if len(dropped_certs) >= min_cert_drop:
        events.append(
            _event(
                event_type="credential_relaxation",
                position_id=position_id,
                from_version_id=from_version_id,
                to_version_id=to_version_id,
                source_entities=[
                    {"kind": "certificate", "certificates": sorted(before_cert_set)}
                ],
                target_entities=[
                    {"kind": "certificate", "certificates": sorted(after_cert_set)}
                ],
                confidence=_bounded(
                    len(dropped_certs) / max(len(before_cert_set), 1) * 0.80
                ),
                magnitude=min(1.0, len(dropped_certs) / 5.0),
                evidence={
                    "lineage": lineage,
                    "source_relations": [
                        {"certificates": sorted(before_cert_set)}
                    ],
                    "target_relations": [
                        {"certificates": sorted(after_cert_set)}
                    ],
                    "source": "requirement_profile_certificate_comparison",
                },
                reason=(
                    f"certificate requirements relaxed: "
                    f"{len(dropped_certs)} certificate(s) no longer required "
                    f"({', '.join(sorted(dropped_certs)[:5])}"
                    + ("..." if len(dropped_certs) > 5 else "")
                    + ")"
                ),
                detector_version=detector_version,
                config_version=config_version,
                created_at=created_at,
                metadata={
                    "atomic_signals": ["certificate_requirement_relaxation"],
                    "credential_relaxation": {
                        "dropped_certificates": sorted(dropped_certs),
                        "before_certificate_count": len(before_cert_set),
                        "after_certificate_count": len(after_cert_set),
                    },
                },
                metrics={
                    "dropped_certificate_count": len(dropped_certs),
                    "before_certificate_count": len(before_cert_set),
                    "after_certificate_count": len(after_cert_set),
                },
                confidence_available=bool(before_cert_set or after_cert_set),
                confidence_source=(
                    "computed" if (before_cert_set or after_cert_set)
                    else "EVIDENCE_QUALITY_UNAVAILABLE"
                ),
                replay_context=replay_context,
            )
        )

    return events


def detect_evolution_events(
    before_snapshot: Mapping[str, object],
    after_snapshot: Mapping[str, object],
    *,
    position_id: str,
    from_version_id: int,
    to_version_id: int,
    config: Mapping[str, object] | None = None,
    created_at: datetime | None = None,
) -> list[dict]:
    merged = _merged_config(config)
    before = _normalize_snapshot(before_snapshot)
    after = _normalize_snapshot(after_snapshot)
    detector_version = str(merged["detector_version"])
    config_version = str(merged["config_version"])
    replay_ctx = _replay_context(merged)
    if replay_ctx is not None:
        config_version = f"{config_version} [replay:{detector_version}]"
        detector_version = f"{detector_version} [replay]"
    timestamp = created_at or EPOCH
    events: list[dict] = []

    signals = _atomic_skill_signals(before, after, merged)
    emergence = [
        item for item in signals
        if item.kind == "skill_emergence" and not item.coverage_driven
    ]
    decline = [
        item for item in signals
        if item.kind == "skill_decline" and not item.coverage_driven
    ]
    lineage = {
        "position_id": position_id,
        "from_version_id": from_version_id,
        "to_version_id": to_version_id,
    }

    def _signal_confidence_available(signal: AtomicSkillSignal) -> bool:
        return signal.confidence_available and signal.evidence_available

    def _signal_confidence_source(signal: AtomicSkillSignal) -> str:
        parts: list[str] = []
        if not signal.evidence_available:
            parts.append("EVIDENCE_QUALITY_UNAVAILABLE")
        if not signal.confidence_available:
            parts.append("CONFIDENCE_UNAVAILABLE")
        return ";".join(parts) if parts else "computed"

    for signal in emergence:
        skill = _relation_payload(signal)
        reason = (
            f"skill {skill.get('canonical_name') or signal.skill_id} ({signal.skill_id}) "
            f"emerged: weight {signal.before_weight} -> {signal.after_weight}"
        )
        events.append(
            _event(
                event_type="skill_emergence",
                position_id=position_id,
                from_version_id=from_version_id,
                to_version_id=to_version_id,
                source_entities=[],
                target_entities=[skill],
                confidence=signal.confidence,
                magnitude=min(1.0, abs(signal.delta)),
                evidence={
                    "lineage": lineage,
                    "source_relations": [],
                    "target_relations": [skill],
                    "source": "graph_version_snapshot_diff",
                },
                reason=reason,
                detector_version=detector_version,
                config_version=config_version,
                created_at=timestamp,
                metadata={"atomic_signals": [signal.kind]},
                metrics={
                    "before_weight": signal.before_weight,
                    "after_weight": signal.after_weight,
                    "delta": round(signal.delta, 6),
                    "evidence_quality": _evidence_quality_details(signal.relation),
                },
                confidence_available=_signal_confidence_available(signal),
                confidence_source=_signal_confidence_source(signal),
                replay_context=replay_ctx,
            )
        )

    for signal in decline:
        skill = _relation_payload(signal)
        reason = (
            f"skill {skill.get('canonical_name') or signal.skill_id} ({signal.skill_id}) "
            f"declined: weight {signal.before_weight} -> {signal.after_weight}"
        )
        events.append(
            _event(
                event_type="skill_decline",
                position_id=position_id,
                from_version_id=from_version_id,
                to_version_id=to_version_id,
                source_entities=[skill],
                target_entities=[],
                confidence=signal.confidence,
                magnitude=min(1.0, abs(signal.delta)),
                evidence={
                    "lineage": lineage,
                    "source_relations": [skill],
                    "target_relations": [],
                    "source": "graph_version_snapshot_diff",
                },
                reason=reason,
                detector_version=detector_version,
                config_version=config_version,
                created_at=timestamp,
                metadata={"atomic_signals": [signal.kind]},
                metrics={
                    "before_weight": signal.before_weight,
                    "after_weight": signal.after_weight,
                    "delta": round(signal.delta, 6),
                    "evidence_quality": _evidence_quality_details(signal.relation),
                },
                confidence_available=_signal_confidence_available(signal),
                confidence_source=_signal_confidence_source(signal),
                replay_context=replay_ctx,
            )
        )

    replacements = _detect_replacements(
        decline,
        emergence,
        position_id,
        from_version_id,
        to_version_id,
        lineage,
        detector_version,
        config_version,
        timestamp,
        merged,
        replay_ctx,
    )
    events.extend(replacements)

    stack_events = _detect_stack_migrations(
        replacements,
        position_id,
        from_version_id,
        to_version_id,
        lineage,
        detector_version,
        config_version,
        timestamp,
        merged,
        replay_ctx,
    )
    events.extend(stack_events)

    responsibility_event = _responsibility_shift_event(
        before,
        after,
        position_id,
        from_version_id,
        to_version_id,
        lineage,
        detector_version,
        config_version,
        timestamp,
        merged,
        replay_ctx,
    )
    if responsibility_event is not None:
        events.append(responsibility_event)

    role_event = _role_breadth_event(
        before,
        after,
        signals,
        position_id,
        from_version_id,
        to_version_id,
        lineage,
        detector_version,
        config_version,
        timestamp,
        merged,
        replay_ctx,
    )
    if role_event is not None:
        events.append(role_event)

    split_merge_events = _position_split_merge_events(
        before,
        after,
        position_id,
        from_version_id,
        to_version_id,
        lineage,
        detector_version,
        config_version,
        timestamp,
        merged,
        replay_ctx,
    )
    events.extend(split_merge_events)

    before_name = _position_name(before)
    after_name = _position_name(after)
    if before_name and after_name and before_name != after_name:
        events.append(
            _event(
                event_type="position_rename",
                position_id=position_id,
                from_version_id=from_version_id,
                to_version_id=to_version_id,
                source_entities=[before_name],
                target_entities=[after_name],
                confidence=1.0,
                magnitude=1.0,
                evidence={
                    "lineage": lineage,
                    "source_relations": [before_name],
                    "target_relations": [after_name],
                    "source": "graph_version_position_name",
                },
                reason=f"position renamed from {before_name} to {after_name}",
                detector_version=detector_version,
                config_version=config_version,
                created_at=timestamp,
                metadata={"atomic_signals": ["position_name_change"]},
                confidence_available=True,
                confidence_source="directly_observed",
                replay_context=replay_ctx,
            )
        )

    # — A18: coverage_artifact — skills whose delta is primarily from
    # source/enterprise/sample coverage changes, not structural change.
    ca_cfg = dict(merged.get("coverage_artifact", {}))
    ca_min_weight = float(ca_cfg.get("min_weight_delta", 0.05))
    ca_min_src = int(ca_cfg.get("min_source_delta", 1))
    ca_min_ent = int(ca_cfg.get("min_enterprise_delta", 1))
    ca_min_smp = int(ca_cfg.get("min_sample_delta", 3))
    ca_ratio = float(ca_cfg.get("coverage_explanation_ratio", 0.5))

    # build relation maps once
    before_relations_map = {
        str(item["skill_id"]): item
        for item in before.get("skill_relations", [])
        if isinstance(item, Mapping) and item.get("skill_id")
    }
    after_relations_map = {
        str(item["skill_id"]): item
        for item in after.get("skill_relations", [])
        if isinstance(item, Mapping) and item.get("skill_id")
    }
    for signal in signals:
        if signal.before_weight is None or signal.after_weight is None:
            continue
        weight_delta = abs(signal.delta)
        if weight_delta < ca_min_weight:
            continue
        before_item = before_relations_map.get(signal.skill_id)
        after_item_i = after_relations_map.get(signal.skill_id)
        before_stats = (before_item or {}).get("statistics", {})
        after_stats = (after_item_i or {}).get("statistics", {})
        if not isinstance(before_stats, Mapping) or not isinstance(after_stats, Mapping):
            continue

        src_before = int(before_stats.get("source_diversity", 0))
        src_after = int(after_stats.get("source_diversity", 0))
        ent_before = int(before_stats.get("enterprise_coverage", 0))
        ent_after = int(after_stats.get("enterprise_coverage", 0))
        smp_before = int(before_stats.get("support_document_count", 0))
        smp_after = int(after_stats.get("support_document_count", 0))

        src_delta = abs(src_after - src_before)
        ent_delta = abs(ent_after - ent_before)
        smp_delta = abs(smp_after - smp_before)

        coverage_change_count = sum([
            1 if src_delta >= ca_min_src else 0,
            1 if ent_delta >= ca_min_ent else 0,
            1 if smp_delta >= ca_min_smp else 0,
        ])
        if coverage_change_count >= 2:
            skill = _relation_payload(signal) if signal.after_weight is not None else (
                _relation_payload(signal)
            )
            reason = (
                f"weight change ({signal.before_weight:.4f}→{signal.after_weight:.4f}) "
                f"primarily explained by coverage delta: "
                f"source_diversity {src_before}→{src_after} (Δ{src_delta}), "
                f"enterprise_coverage {ent_before}→{ent_after} (Δ{ent_delta}), "
                f"support_documents {smp_before}→{smp_after} (Δ{smp_delta})"
            )
            events.append(
                _event(
                    event_type="coverage_artifact",
                    position_id=position_id,
                    from_version_id=from_version_id,
                    to_version_id=to_version_id,
                    source_entities=[skill] if signal.before_weight is not None else [],
                    target_entities=[skill] if signal.after_weight is not None else [],
                    confidence=_bounded(
                        coverage_change_count / 3.0
                        * (weight_delta / max(ca_min_weight, 0.01))
                    ),
                    magnitude=min(1.0, weight_delta),
                    evidence={
                        "lineage": lineage,
                        "source_relations": [skill],
                        "target_relations": [skill],
                        "source": "coverage_metric_comparison",
                    },
                    reason=reason,
                    detector_version=detector_version,
                    config_version=config_version,
                    created_at=timestamp,
                    metadata={
                        "atomic_signals": [signal.kind or "weight_change"],
                        "coverage_deltas": {
                            "source_diversity_delta": src_delta,
                            "enterprise_coverage_delta": ent_delta,
                            "support_document_delta": smp_delta,
                            "coverage_change_count": coverage_change_count,
                        },
                    },
                    metrics={
                        "before_weight": signal.before_weight,
                        "after_weight": signal.after_weight,
                        "weight_delta": round(weight_delta, 6),
                        "evidence_quality": _evidence_quality_details(signal.relation),
                    },
                    confidence_available=True,
                    confidence_source="computed",
                    replay_context=replay_ctx,
                )
            )

    # — A19: seniority_inflation — experience years requirements decreased —
    seniority_events = _seniority_inflation_events(
        before, after,
        position_id, from_version_id, to_version_id, lineage,
        detector_version, config_version, timestamp, merged, replay_ctx,
    )
    events.extend(seniority_events)

    # — A19: credential_relaxation — degree/certificate requirements relaxed —
    credential_events = _credential_relaxation_events(
        before, after,
        position_id, from_version_id, to_version_id, lineage,
        detector_version, config_version, timestamp, merged, replay_ctx,
    )
    events.extend(credential_events)

    events.sort(
        key=lambda item: (
            item["event_type"],
            str(item["source_entities"]),
            str(item["target_entities"]),
        )
    )
    for index, event in enumerate(events, start=1):
        event["event_id"] = (
            f"evt-{from_version_id}-{to_version_id}-{event['event_type']}-{index:03d}"
        )
    return events


def _detect_replacements(
    decline: list[AtomicSkillSignal],
    emergence: list[AtomicSkillSignal],
    position_id: str,
    from_version_id: int,
    to_version_id: int,
    lineage: Mapping[str, object],
    detector_version: str,
    config_version: str,
    created_at: datetime,
    config: Mapping[str, object],
    replay_context: Mapping[str, object] | None = None,
) -> list[dict]:
    events: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for old in decline:
        for new in emergence:
            if old.skill_id == new.skill_id:
                continue
            if not _related_context(old.relation, new.relation):
                continue
            key = (old.skill_id, new.skill_id)
            if key in seen:
                continue
            seen.add(key)
            semantic = 1.0 if _related_context(old.relation, new.relation) else 0.4
            signal_strength = _bounded(
                (abs(old.delta) + abs(new.delta))
                / max(
                    float(config["emergence_min_delta"])
                    + float(config["decline_min_delta"]),
                    0.01,
                )
                * 0.5
            )
            cross = (
                0.9
                if _confidence_value(old.relation) >= 0.6
                and _confidence_value(new.relation) >= 0.6
                else 0.7
            )
            evidence_quality = min(old.evidence_quality, new.evidence_quality)
            confidence = _confidence(
                signal_strength, cross, evidence_quality, semantic, config
            )
            if confidence < float(config["replacement_min_confidence"]):
                continue
            repl_confidence_available = (
                old.confidence_available and new.confidence_available
                and old.evidence_available and new.evidence_available
            )
            repl_confidence_parts: list[str] = []
            if not old.evidence_available or not new.evidence_available:
                repl_confidence_parts.append("EVIDENCE_QUALITY_UNAVAILABLE")
            if not old.confidence_available or not new.confidence_available:
                repl_confidence_parts.append("CONFIDENCE_UNAVAILABLE")
            repl_confidence_source = ";".join(repl_confidence_parts) if repl_confidence_parts else "computed"
            source = _relation_payload(old)
            target = _relation_payload(new)
            events.append(
                _event(
                    event_type="skill_replacement",
                    position_id=position_id,
                    from_version_id=from_version_id,
                    to_version_id=to_version_id,
                    source_entities=[source],
                    target_entities=[target],
                    confidence=confidence,
                    magnitude=min(
                        1.0, (abs(old.delta) + abs(new.delta)) / 2.0
                    ),
                    evidence={
                        "lineage": lineage,
                        "source_relations": [source],
                        "target_relations": [target],
                        "related_context": _semantic_context(old.relation),
                        "source": "graph_version_snapshot_diff",
                    },
                    reason=(
                        f"skill {source.get('canonical_name') or old.skill_id} declined "
                        f"while {target.get('canonical_name') or new.skill_id} emerged "
                        f"in related context {_semantic_context(old.relation)}"
                    ),
                    detector_version=detector_version,
                    config_version=config_version,
                    created_at=created_at,
                    metadata={
                        "atomic_signals": ["skill_decline", "skill_emergence"]
                    },
                    metrics={
                        "decline_delta": old.delta,
                        "emergence_delta": new.delta,
                        "evidence_quality": {
                            "decline": _evidence_quality_details(old.relation),
                            "emergence": _evidence_quality_details(new.relation),
                            "combined": evidence_quality,
                        },
                    },
                    confidence_available=repl_confidence_available,
                    confidence_source=repl_confidence_source,
                    replay_context=replay_context,
                )
            )
    return events


def _detect_stack_migrations(
    replacements: list[dict],
    position_id: str,
    from_version_id: int,
    to_version_id: int,
    lineage: Mapping[str, object],
    detector_version: str,
    config_version: str,
    created_at: datetime,
    config: Mapping[str, object],
    replay_context: Mapping[str, object] | None = None,
) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for event in replacements:
        context = event["evidence"].get("related_context") or "unknown"
        grouped.setdefault(str(context), []).append(event)
    events: list[dict] = []
    minimum = int(config["stack_min_related_replacements"])
    for context in sorted(grouped):
        items = grouped[context]
        if len(items) < minimum:
            continue
        sources = []
        targets = []
        for item in items:
            sources.extend(item["source_entities"])
            targets.extend(item["target_entities"])
        unique_sources = sorted(
            sources, key=lambda value: str(value.get("skill_id"))
        )
        unique_targets = sorted(
            targets, key=lambda value: str(value.get("skill_id"))
        )
        all_available = all(
            item.get("confidence_available", True) for item in items
        )
        unavailable_sources: set[str] = set()
        for item in items:
            src = item.get("confidence_source", "")
            if src and src != "computed":
                unavailable_sources.add(src)
        stack_confidence_source = ";".join(sorted(unavailable_sources)) if unavailable_sources else "computed"
        events.append(
            _event(
                event_type="technology_stack_migration",
                position_id=position_id,
                from_version_id=from_version_id,
                to_version_id=to_version_id,
                source_entities=unique_sources,
                target_entities=unique_targets,
                confidence=round(
                    sum(item["confidence"] for item in items) / len(items) * 0.9,
                    6,
                ),
                magnitude=round(
                    sum(item["magnitude"] for item in items) / len(items), 6
                ),
                evidence={
                    "lineage": lineage,
                    "source_relations": unique_sources,
                    "target_relations": unique_targets,
                    "related_context": context,
                    "source": "composite_skill_replacement_events",
                },
                reason=(
                    f"technology stack migration in context {context}: "
                    f"{[str(item.get('skill_id')) for item in unique_sources]} -> "
                    f"{[str(item.get('skill_id')) for item in unique_targets]}"
                ),
                detector_version=detector_version,
                config_version=config_version,
                created_at=created_at,
                metadata={
                    "atomic_signals": [
                        "skill_replacement",
                        "skill_decline",
                        "skill_emergence",
                    ],
                    "replacement_event_keys": [
                        (
                            str(item["source_entities"][0].get("skill_id")),
                            str(item["target_entities"][0].get("skill_id")),
                        )
                        for item in items
                    ],
                },
                confidence_available=all_available,
                confidence_source=stack_confidence_source,
                replay_context=replay_context,
            )
        )
    return events


def _responsibility_shift_event(
    before: Mapping[str, object],
    after: Mapping[str, object],
    position_id: str,
    from_version_id: int,
    to_version_id: int,
    lineage: Mapping[str, object],
    detector_version: str,
    config_version: str,
    created_at: datetime,
    config: Mapping[str, object],
    replay_context: Mapping[str, object] | None = None,
) -> dict | None:
    before_set = _responsibility_set(before)
    after_set = _responsibility_set(after)
    removed = sorted(before_set - after_set)
    added = sorted(after_set - before_set)
    union = before_set | after_set
    similarity = len(before_set & after_set) / len(union) if union else 1.0
    changed = len(removed) + len(added)
    min_overlap = float(config.get("responsibility_min_overlap", 0.0))
    if (
        changed < int(config["responsibility_min_changed"])
        or similarity >= float(config["responsibility_similarity_threshold"])
        # 完全无重叠（similarity<=min_overlap）意味着职责文本零共同点，
        # 属于抽样换血而非"职责迁移"；单边变化（removed/added 为 0）是增长/收缩，
        # 也不是迁移。
        or similarity <= min_overlap
        or not removed
        or not added
    ):
        return None
    magnitude = changed / max(len(union), 1)
    confidence = _confidence(magnitude, 1.0, 0.8, 1.0, config)
    return _event(
        event_type="responsibility_shift",
        position_id=position_id,
        from_version_id=from_version_id,
        to_version_id=to_version_id,
        source_entities=list(removed),
        target_entities=list(added),
        confidence=confidence,
        magnitude=magnitude,
        evidence={
            "lineage": lineage,
            "source_relations": list(removed),
            "target_relations": list(added),
            "similarity": round(similarity, 6),
            "source": "responsibility_set_diff",
        },
        reason=(
            f"responsibility profile shifted: removed {len(removed)}, "
            f"added {len(added)}, similarity {round(similarity, 6)}"
        ),
        detector_version=detector_version,
        config_version=config_version,
        created_at=created_at,
        metadata={"atomic_signals": ["responsibility_removed", "responsibility_added"]},
        metrics={
            "removed_count": len(removed),
            "added_count": len(added),
            "similarity": round(similarity, 6),
        },
        confidence_available=True,
        confidence_source="fixed_estimate",
        replay_context=replay_context,
    )


def _role_breadth_event(
    before: Mapping[str, object],
    after: Mapping[str, object],
    signals: list[AtomicSkillSignal],
    position_id: str,
    from_version_id: int,
    to_version_id: int,
    lineage: Mapping[str, object],
    detector_version: str,
    config_version: str,
    created_at: datetime,
    config: Mapping[str, object],
    replay_context: Mapping[str, object] | None = None,
) -> dict | None:
    coverage_driven_additions = {
        signal.skill_id
        for signal in signals
        if signal.coverage_driven and signal.before_weight is None
    }
    coverage_driven_removals = {
        signal.skill_id
        for signal in signals
        if signal.coverage_driven and signal.after_weight is None
    }
    before_relations = {
        str(item["skill_id"]): item
        for item in before.get("skill_relations", [])
        if isinstance(item, Mapping) and item.get("skill_id")
        and str(item["skill_id"]) not in coverage_driven_removals
    }
    after_relations = {
        str(item["skill_id"]): item
        for item in after.get("skill_relations", [])
        if isinstance(item, Mapping) and item.get("skill_id")
        and str(item["skill_id"]) not in coverage_driven_additions
    }
    # 覆盖可比性门禁：任一版本样本量不足时，能力广度变化不可靠，不判 expansion/contraction。
    min_comparable = int(config.get("min_comparable_samples", 3))
    if (
        _snapshot_sample_count(before) < min_comparable
        or _snapshot_sample_count(after) < min_comparable
    ):
        return None
    before_categories = {
        code
        for item in before_relations.values()
        for code in _category_codes(item)
    }
    after_categories = {
        code
        for item in after_relations.values()
        for code in _category_codes(item)
    }

    def normalized_change(left: int, right: int) -> float:
        if left == 0 and right == 0:
            return 0.0
        if left == 0:
            return 1.0
        return (right - left) / left

    skill_change = normalized_change(len(before_relations), len(after_relations))
    responsibility_change = normalized_change(
        len(_responsibility_set(before)), len(_responsibility_set(after))
    )
    category_change = normalized_change(
        len(before_categories), len(after_categories)
    )
    trusted_signals = [signal for signal in signals if not signal.coverage_driven]
    positive = sum(
        signal.delta for signal in trusted_signals if signal.delta > 0
    )
    negative = sum(
        abs(signal.delta) for signal in trusted_signals if signal.delta < 0
    )
    net_weight_change = _bounded(positive) - _bounded(negative)
    breadth_score = (
        0.35 * skill_change
        + 0.25 * responsibility_change
        + 0.20 * category_change
        + 0.20 * net_weight_change
    )
    noise = float(config["noise_min_absolute_weight_change"])
    has_material_change = (
        abs(skill_change) > 0
        or abs(responsibility_change) > 0
        or abs(category_change) > 0
        or positive + negative > noise
    )
    if not has_material_change:
        return None
    if breadth_score >= float(config["role_expansion_threshold"]):
        event_type = "role_expansion"
    elif breadth_score <= -float(config["role_contraction_threshold"]):
        event_type = "role_contraction"
    else:
        return None
    before_counts = {
        "skill_count": len(before_relations),
        "responsibility_count": len(_responsibility_set(before)),
        "category_count": len(before_categories),
        "positive_weight_change": round(positive, 6),
        "negative_weight_change": round(negative, 6),
        "net_weight_change": round(net_weight_change, 6),
    }
    after_counts = {
        "skill_count": len(after_relations),
        "responsibility_count": len(_responsibility_set(after)),
        "category_count": len(after_categories),
        "positive_weight_change": round(positive, 6),
        "negative_weight_change": round(negative, 6),
        "net_weight_change": round(net_weight_change, 6),
    }
    magnitude = abs(breadth_score)
    confidence = _confidence(magnitude, 1.0, 0.8, 1.0, config)
    return _event(
        event_type=event_type,
        position_id=position_id,
        from_version_id=from_version_id,
        to_version_id=to_version_id,
        source_entities=[before_counts],
        target_entities=[after_counts],
        confidence=confidence,
        magnitude=magnitude,
        evidence={
            "lineage": lineage,
            "source_relations": [before_counts],
            "target_relations": [after_counts],
            "source": "capability_breadth_metrics",
        },
        reason=(
            f"role capability breadth {event_type.replace('role_', '')}: "
            f"skill {round(skill_change, 4)}, responsibility "
            f"{round(responsibility_change, 4)}, category "
            f"{round(category_change, 4)}, weight {round(positive, 4)}/{round(negative, 4)}"
        ),
        detector_version=detector_version,
        config_version=config_version,
        created_at=created_at,
        metadata={"atomic_signals": ["skill_emergence", "skill_decline"]},
        metrics={
            "skill_breadth_change": round(skill_change, 6),
            "responsibility_breadth_change": round(responsibility_change, 6),
            "category_breadth_change": round(category_change, 6),
            "importance_change": round(net_weight_change, 6),
            "breadth_score": round(breadth_score, 6),
        },
        confidence_available=True,
        confidence_source="fixed_estimate",
        replay_context=replay_context,
    )


def _position_split_merge_events(
    before: Mapping[str, object],
    after: Mapping[str, object],
    position_id: str,
    from_version_id: int,
    to_version_id: int,
    lineage: Mapping[str, object],
    detector_version: str,
    config_version: str,
    created_at: datetime,
    config: Mapping[str, object],
    replay_context: Mapping[str, object] | None = None,
) -> list[dict]:
    """检测岗位拆分/合并。

    position_split: 同一类别中 >=3 个技能集体消失 + 职责减少 >=2 个 + 技能总数下降 >=25%
    position_merge: 新类别中 >=3 个技能集体出现 + 职责增加 >=2 个 + 技能总数上升 >=25%
    """
    # 覆盖可比性门禁：任一版本样本量不足时，拆分/合并判定不可靠。
    min_comparable = int(config.get("min_comparable_samples", 3))
    if (
        _snapshot_sample_count(before) < min_comparable
        or _snapshot_sample_count(after) < min_comparable
    ):
        return []

    before_relations = {
        str(item["skill_id"]): item
        for item in before.get("skill_relations", [])
        if isinstance(item, Mapping) and item.get("skill_id")
    }
    after_relations = {
        str(item["skill_id"]): item
        for item in after.get("skill_relations", [])
        if isinstance(item, Mapping) and item.get("skill_id")
    }

    before_ids = set(before_relations.keys())
    after_ids = set(after_relations.keys())
    removed_ids = before_ids - after_ids
    added_ids = after_ids - before_ids

    before_resp = _responsibility_set(before)
    after_resp = _responsibility_set(after)
    removed_resp = before_resp - after_resp
    added_resp = after_resp - before_resp

    events: list[dict] = []

    # ── position_split: coherent cluster disappearing ──
    if removed_ids:
        removed_by_cat: dict[str, list[dict]] = {}
        for sid in removed_ids:
            item = before_relations[sid]
            for code in _category_codes(item):
                removed_by_cat.setdefault(code, []).append(item)

        largest_cluster = max(removed_by_cat.values(), key=len, default=[])
        min_skills = int(config["split_min_category_skills_removed"])
        min_skill_frac = float(config["split_min_skill_fraction"])
        min_resp = int(config["split_min_responsibilities_removed"])

        if (len(largest_cluster) >= min_skills
                and len(removed_resp) >= min_resp
                and len(before_relations) > 0
                and len(removed_ids) / len(before_relations) >= min_skill_frac):
            category = _semantic_context(largest_cluster[0])
            skill_payloads = [deepcopy(dict(item)) for item in largest_cluster]
            magnitude = min(1.0, len(removed_ids) / max(len(before_relations), 1))
            confidence = _confidence(magnitude, 0.85, 0.7, 0.85, config)
            events.append(
                _event(
                    event_type="position_split",
                    position_id=position_id,
                    from_version_id=from_version_id,
                    to_version_id=to_version_id,
                    source_entities=skill_payloads,
                    target_entities=[],
                    confidence=confidence,
                    magnitude=magnitude,
                    evidence={
                        "lineage": lineage,
                        "source_relations": skill_payloads,
                        "target_relations": [],
                        "split_category": category,
                        "source": "coherent_skill_cluster_removal",
                    },
                    reason=(
                        f"coherent cluster of {len(largest_cluster)} skills in "
                        f"category {category} disappeared ({len(removed_ids)} total removed, "
                        f"{len(removed_resp)} responsibilities removed); "
                        f"evidence suggests position split"
                    ),
                    detector_version=detector_version,
                    config_version=config_version,
                    created_at=created_at,
                    metadata={"atomic_signals": ["skill_removed", "responsibility_removed"]},
                    metrics={
                        "removed_skill_count": len(removed_ids),
                        "cluster_skill_count": len(largest_cluster),
                        "cluster_category": category,
                        "removed_responsibility_count": len(removed_resp),
                        "total_before_skill_count": len(before_relations),
                        "skill_fraction_removed": round(
                            len(removed_ids) / max(len(before_relations), 1), 6
                        ),
                    },
                    confidence_available=True,
                    confidence_source="fixed_estimate",
                    replay_context=replay_context,
                )
            )

    # ── position_merge: coherent cluster appearing ──
    if added_ids:
        added_by_cat: dict[str, list[dict]] = {}
        for sid in added_ids:
            item = after_relations[sid]
            for code in _category_codes(item):
                added_by_cat.setdefault(code, []).append(item)

        # 找最大新增类别（且该类别在 before 中不存在或极少）
        largest_new = max(added_by_cat.values(), key=len, default=[])
        min_skills = int(config["merge_min_category_skills_added"])
        min_skill_frac = float(config["merge_min_skill_fraction"])
        min_resp = int(config["merge_min_responsibilities_added"])

        # 验证该类别确实是"新领域"：before 中该类技能 <=1
        cluster_cat = (
            _semantic_context(largest_new[0]) if largest_new else ""
        )
        before_in_cat = sum(
            1 for item in before_relations.values()
            if cluster_cat in _category_codes(item)
        )

        if (len(largest_new) >= min_skills
                and len(added_resp) >= min_resp
                and before_in_cat <= 1
                and len(after_relations) > 0
                and len(added_ids) / len(after_relations) >= min_skill_frac):
            skill_payloads = [deepcopy(dict(item)) for item in largest_new]
            magnitude = min(1.0, len(added_ids) / max(len(after_relations), 1))
            confidence = _confidence(magnitude, 0.85, 0.7, 0.85, config)
            events.append(
                _event(
                    event_type="position_merge",
                    position_id=position_id,
                    from_version_id=from_version_id,
                    to_version_id=to_version_id,
                    source_entities=[],
                    target_entities=skill_payloads,
                    confidence=confidence,
                    magnitude=magnitude,
                    evidence={
                        "lineage": lineage,
                        "source_relations": [],
                        "target_relations": skill_payloads,
                        "merge_category": cluster_cat,
                        "before_skills_in_category": before_in_cat,
                        "source": "coherent_skill_cluster_addition",
                    },
                    reason=(
                        f"coherent cluster of {len(largest_new)} new skills in "
                        f"category {cluster_cat} appeared (only {before_in_cat} existed before, "
                        f"{len(added_ids)} total added, {len(added_resp)} responsibilities added); "
                        f"evidence suggests position merge"
                    ),
                    detector_version=detector_version,
                    config_version=config_version,
                    created_at=created_at,
                    metadata={"atomic_signals": ["skill_added", "responsibility_added"]},
                    metrics={
                        "added_skill_count": len(added_ids),
                        "cluster_skill_count": len(largest_new),
                        "cluster_category": cluster_cat,
                        "before_skills_in_category": before_in_cat,
                        "added_responsibility_count": len(added_resp),
                        "total_after_skill_count": len(after_relations),
                        "skill_fraction_added": round(
                            len(added_ids) / max(len(after_relations), 1), 6
                        ),
                    },
                    confidence_available=True,
                    confidence_source="fixed_estimate",
                    replay_context=replay_context,
                )
            )

    return events
