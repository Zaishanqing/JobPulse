# Evidence 闭环技术说明

## 目标

系统内所有“结论”都必须能回溯到原文 Evidence：谁在什么时间、什么版本、哪家来源的
JD / CV 中说了什么。Evidence 是抽取、图谱、匹配、RAG 与人工审核的共同语言。

## Evidence 身份与血缘

- 每条 Evidence 包含 `source_id / quote / start / end / alignment / occurrence_index`
  等必填字段，引用必须精确对齐原文 span。
- JD / CV 抽取输出的 Evidence 与发布后的岗位画像、匹配评估、演化事件绑定，
  形成不可变血缘链。
- GraphVersion 是知识图谱的唯一版本语义，Published Profile、Matching 与 Trend
  共用同一 GraphVersion 身份。

## Evidence 独立性

招聘数据中同一事实会被多个来源、企业和模板重复表述。系统对 Evidence 做独立性
聚类：

- 自动合并只在证据充分相似时发生，宁可保守也不误合并；
- 相似但不确定的对子进入人工审核候选；
- 输出独立簇、有效样本量（N_eff）与来源/企业/模板敏感性分析。

该机制支撑“可信结论闭环”：引用独立证据数时，同时给出自动合并与审核后的口径。

## Evidence RAG

Evidence RAG 走生产链路：metadata 硬过滤 → BM25 + 向量混合检索 → 特征重排 →
概念充分性门控 → 基于检索结果的答案生成。系统区分：

- 检索质量：召回、第一名精确率、可答性判定、拒答准确率、租户/版本隔离；
- 答案质量：引用精确率、Faithfulness（答案断言必须被所引 Evidence 支撑）、
  无支撑断言率。

不同租户与版本之间严格隔离，未检索到足够 Evidence 时明确拒答，不猜测。

## 人工审核（HITL）

审核任务按“预期价值”排序：优先处理能释放阻塞、修正下游结论、可复用的任务。
审核决定、处理时间与理由均持久化，形成可审计的价值闭环。

## Insight Card

治理结论以 Insight Card 输出，统一呈现：结论、Evidence、覆盖风险、不确定性与
人工审核状态，字段契约固定于 `insight-card-contract.snapshot.json`。
