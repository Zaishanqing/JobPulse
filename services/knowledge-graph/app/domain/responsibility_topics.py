"""Deterministic semantic aggregation for published position responsibilities."""

from __future__ import annotations

from collections.abc import Iterable
import re


# Topics are internal grouping coordinates. They must never become the formal
# responsibility text consumed by Matching; every group selects a source sentence.
RESPONSIBILITY_TOPIC_PATTERNS = (
    ("训练与对齐", ("预训练", "后训练", "微调", "sft", "rlhf", "dpo", "ppo", "grpo", "强化学习", "对齐", "reward model", "奖励模型", "蒸馏")),
    ("数据构建与治理", ("数据集", "数据构建", "数据准备", "数据治理", "数据清洗", "数据合成", "合成数据", "标注", "数据配比", "数据质量", "质检")),
    ("模型架构研究", ("模型架构", "基座架构", "moe", "attention", "scaling law", "tokenizer", "diffusion", "模型结构", "one model")),
    ("系统与方案设计", ("系统架构", "架构设计", "技术架构", "系统设计", "方案设计", "技术选型", "接口设计")),
    ("智能体与工具调用", ("agent", "智能体", "工具调用", "tool-use", "function-calling", "任务规划", "任务拆解", "planner", "mcp", "多智能体")),
    ("检索与知识增强", ("rag", "检索", "召回", "知识增强", "知识图谱", "搜索问答", "信息检索")),
    ("评测与效果迭代", ("评测", "评估体系", "效果评估", "ab实验", "a/b实验", "指标", "badcase", "效果验证", "持续跟踪", "负反馈")),
    ("训练基础设施", ("ai infra", "训练框架", "分布式", "集群", "并行策略", "显存", "跨节点", "kernel", "算子", "异构", "国产芯片", "资源调度")),
    ("推理与性能优化", ("推理性能", "推理优化", "性能优化", "性能调优", "吞吐", "高并发", "资源利用率", "训练稳定性", "推理效率", "性能与稳定性")),
    ("服务化与生产工程", ("服务部署", "线上部署", "服务化", "api封装", "工程化", "mlops", "生产级", "工具链", "故障诊断", "线上模型", "规模化使用")),
    ("业务应用与产品落地", ("业务场景", "产品化", "商业闭环", "应用落地", "业务落地", "工程落地", "规模化应用", "产品体系", "产品能力", "业务流程", "用户体验")),
    ("安全风控与合规", ("风控", "安全性", "内容安全", "安全对齐", "合规", "对抗防御", "风险研判", "幻觉检测", "治理体系")),
    ("生成与内容理解", ("内容生成", "文本生成", "图文生成", "文案生成", "多模态", "内容理解", "对话", "机器翻译", "生成能力")),
    ("算法建模与优化", ("算法研发", "算法模型", "建模", "模型优化", "模型调优", "模型选型", "预测模型", "uplift", "ctr", "cvr", "ltr")),
    ("技术研究与创新", ("前沿", "研究进展", "技术预研", "技术创新", "探索", "学术", "论文", "专利", "技术趋势", "调研")),
    ("跨团队协作与交付", ("协同", "协作", "跨职能", "跨团队", "产品、工程", "产品和工程", "项目交付", "技术 owner", "主导", "统筹", "团队能力")),
    ("文档与知识沉淀", ("技术文档", "实验报告", "方法论", "技术报告", "开源", "总结", "沉淀")),
)

DEFAULT_RESPONSIBILITY_TOPIC = "综合职责"
_TOKEN = re.compile(r"[a-z0-9+#.-]+|[\u4e00-\u9fff]", re.IGNORECASE)


def _topic_scores(text: str) -> tuple[tuple[int, int, str], ...]:
    lowered = text.casefold()
    return tuple(
        (sum(len(pattern) for pattern in patterns if pattern in lowered), -index, topic)
        for index, (topic, patterns) in enumerate(RESPONSIBILITY_TOPIC_PATTERNS)
    )


def normalize_responsibility_topic(text: str) -> str:
    """Return the strongest stable semantic topic for one source sentence."""
    score, _priority, topic = max(
        _topic_scores(text), default=(0, 0, DEFAULT_RESPONSIBILITY_TOPIC)
    )
    return topic if score > 0 else DEFAULT_RESPONSIBILITY_TOPIC


def _ngrams(text: str) -> frozenset[str]:
    tokens = _TOKEN.findall(text.casefold())
    return frozenset(
        "".join(tokens[index : index + 2])
        for index in range(max(0, len(tokens) - 1))
    )


def _centrality(text: str, peers: Iterable[str]) -> float:
    current = _ngrams(text)
    if not current:
        return 0.0
    total = 0.0
    for peer in peers:
        other = _ngrams(peer)
        union = current | other
        if union:
            total += len(current & other) / len(union)
    return total


def _dedupe_dicts(values: Iterable[object]) -> list[dict]:
    result: list[dict] = []
    seen: set[tuple[object, ...]] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        key = (
            value.get("id"), value.get("evidence_id"), value.get("source_id"),
            value.get("document_id"), value.get("quote"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def merge_responsibility_topics(
    responsibilities: list[dict] | tuple[dict, ...],
) -> list[dict]:
    """Aggregate raw tasks into stable topics with concrete representatives."""
    grouped: dict[str, list[dict]] = {}
    for raw in responsibilities:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("representative_text") or raw.get("text") or "").strip()
        if not text:
            continue
        grouped.setdefault(normalize_responsibility_topic(text), []).append(
            {**raw, "text": text}
        )

    result: list[dict] = []
    for topic, items in grouped.items():
        texts = [str(item["text"]) for item in items]
        representative = max(
            items,
            key=lambda item: (
                int(item.get("support_document_count") or len(item.get("document_ids") or ())),
                round(_centrality(str(item["text"]), texts), 8),
                min(len(str(item["text"])), 160),
                str(item["text"]),
            ),
        )
        document_ids = sorted({
            str(value) for item in items for value in item.get("document_ids") or ()
            if str(value)
        })
        evidence_ids = sorted({
            int(value) if str(value).isdigit() else str(value)
            for item in items for value in item.get("evidence_ids") or ()
        }, key=str)
        skill_ids = sorted({
            str(value) for item in items for value in item.get("skill_ids") or ()
            if str(value)
        })
        source_items = [
            {
                "text": str(item["text"]),
                "requirement_id": item.get("aggregate_id") or item.get("requirement_id"),
                "document_ids": list(item.get("document_ids") or ()),
                "evidence": list(item.get("evidence") or ()),
                "skill_ids": sorted({str(value) for value in item.get("skill_ids") or ()}),
            }
            for item in items
        ]
        result.append({
            "topic": topic,
            "text": str(representative["text"]),
            "representative_text": str(representative["text"]),
            "representative_source_id": representative.get("aggregate_id")
            or representative.get("requirement_id"),
            "document_ids": document_ids,
            "evidence_ids": evidence_ids,
            "source_texts": texts,
            "source_items": source_items,
            "evidence": _dedupe_dicts(
                evidence for item in items for evidence in item.get("evidence") or ()
            ),
            "support_count": len(document_ids),
            "skill_ids": skill_ids,
        })
    return result
