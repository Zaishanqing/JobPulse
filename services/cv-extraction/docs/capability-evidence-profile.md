# 能力证据画像

> 文档类型：设计说明
> 适用范围：`services/cv-extraction/`

## 目标

在现有 Confirmed / Validated CV Snapshot 上增加证据层，区分：

```text
declared_only: 技能栏声明
project_used:  项目中使用
work_used:     工作中使用
owned_component / designed_system / measured_result
```

本层是证据类别，不是 proficiency score，也不替换 Matching 正在使用的现有 CV Profile。

## 数据结构

```text
CVCapabilityEvidenceProfileResult
├─ document_id
├─ taxonomy_version
├─ created_from_snapshot
├─ as_of_date
└─ profiles[]

CapabilityEvidenceProfile
├─ capability_id
├─ skill_id / skill_name
├─ evidence_count
├─ strongest_evidence
└─ evidence_items[]

CapabilityEvidenceItem
├─ evidence_level
├─ context[]
├─ ownership
├─ depth
├─ recency
├─ source_scope
├─ source_experience_id / source_project_id
├─ source_evidence
├─ evidence_lineage[]
└─ source_text
```

## Evidence Level 规则

- `declared_only`：技能栏声明。
- `course_used`：声明 evidence 包含课程 / 学习 / 培训。
- `project_used` / `work_used`：出现在项目或工作 tech_stack。
- `owned_component`：任务文本包含独立、负责、own 等客观动作。
- `designed_system`：任务文本包含设计、架构、pipeline。
- `measured_result`：任务文本包含准确率、指标、提升至 N% 等量化结果。

## Ownership / Context / Depth / Recency

- Ownership 由动作词推导：主导 / 设计 / 独立负责 / 实现 / 参与，不使用分数。
- Context 保留项目名、角色、公司、岗位、任务原文。
- Depth 映射为 declared / used / implemented / designed / led。
- Recency 根据经历结束时间相对 as_of_date 分为 recent / moderate / old / unknown。

## Builder

`src/capability_evidence_profile.py` 的
`build_capability_evidence_profiles(extraction, normalized, as_of_date)`
是 deterministic adapter，不修改原始 snapshot。

## 当前限制

- Evidence Level 和 Ownership 依赖现有 CV 提取的结构化字段与任务文本质量。
- 不引入 LLM 能力总分、向量库、Learning Path 或 Matching 评分变更。
