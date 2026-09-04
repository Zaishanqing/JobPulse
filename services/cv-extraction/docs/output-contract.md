# CV 抽取输出契约

## 岗位分类 v3

HTTP 正式入口为 `POST /api/v3/cv-extractions`。服务先完成 CV 抽取和技能归一化，
再将 `personal_info.expected_position` 与每条 `work_experience.position` 作为独立
角色批量执行 `position-taxonomy.v3.0.0` 分类，结果写入
`normalized_result.position_classifications`。每条记录保留角色类型、来源对象、
来源字段和完整 `job-position-classification.v3`；未解析状态不得绑定
`position_code`。`/api/v2/cv-extractions` 仅保留不含岗位分类的兼容响应。

角色岗位特征使用 `position-taxonomy.v3.0.0`，`canonical_id` 固定为
`null`，身份由 `taxonomy_version + structured_values.position_code` 表达。
`structured_values` 保存分类状态、Top-K 候选、职级、领导范围、技术方向、行业、
观测技能领域、Evidence 和策略版本。重分类写入独立 v3 run，不重新抽取或覆盖 v2。

> 文档类型：契约
> 维护状态：active
> 适用范围：`services/cv-extraction/`
> 事实源：模块代码、配置、Schema 与测试
> 责任角色：模块维护者
> 最后复核：2026-08-09

## 输出

```text
output/runs/<run_id>/
├── manifest.json
├── logs.jsonl
├── research_report.md
├── audit/
├── records/
└── final/
    ├── annotations.jsonl
    ├── annotations_nested.json
    ├── normalized_annotations.jsonl
    ├── normalized_annotations.json
    ├── match_features.jsonl
    ├── match_feature_profiles.json
    ├── capability_verification_profiles.json
    ├── capability_profiles.jsonl
    ├── capability_evidence_links.jsonl
    ├── annotations.xlsx
    ├── review_flags.jsonl
    ├── failed_cases.jsonl
    └── illegal_enum_cases.jsonl
```

`normalized_annotations.*` 直接保存完整标准技能结果：文档级
`skill_taxonomy_version` 固定分类快照；每个技能包含
`identity_resolution_status`、`classification_resolution_status` 和
`classifications`，分类关系使用 `concept_class`、`technology_kind` 与多值
`domain`。身份未解析与分类缺失分别使用 `identity_unresolved` 和
`classification_missing`，不会写入默认类别。输出不再包含旧

## 业务场景维度：已移除

业务场景（`business_scenarios`）不作为正式链路的一等维度存在：
`cv-extraction-http.v1/v2/v3` 的 `CVWorkEntry` / `CVProjectEntry` 不设该字段
（契约基类 `extra="forbid"`），也不设 `tool_source_item_ids`。CV 侧正式可承载
的是 `responsibilities` / `skills` / `tech_stack` 等可证据字段，任务上下文由
责任/项目片段承载。

决策（2026-08 确定）：

- 24-pair 数据集升 `semantic-shadow-dataset.v3`，移除 `business_scenarios`
  标注与场景片段类型；
- Matching 正式评分的 `scenario_results` 保持 inert（恒空），不参与 Semantic
  Shadow 不变性比较；
- seed 报告只对比正式可承载字段（技能、职责、工具引用），不再出现场景漂移。

正式抽取层保持现状，不为场景新增契约字段。
`category_code/subcategory_code`，也不再生成独立分类 sidecar。

Excel 包含经历主体、工作职责、工作成果、项目事实、声明技能、工作技术栈、项目技术栈、归一化技能及其 Evidence 的独立 sheet。

`match_features.jsonl` 是面向向量库和匹配引擎的扁平原子特征流；`match_feature_profiles.json` 按简历分组，便于审计。技能优先使用 `canonical_id` 精确匹配，任务、经历和岗位文本使用 `vector_text`，年限、学历和薪资使用 `structured_values`。技能的每次原文出现仍独立保留，但匹配覆盖度必须按 `structured_values.aggregation_key` 去重；`occurrence_kind` 区分声明、工作和项目证据。任何未知技能、语言或奖项等级都不会写入 `candidate_level`。

岗位特征使用 `position-taxonomy.v3.0.0`。Extraction 不生成或复制主系统 UUID，`canonical_id` 必须为空；岗位身份由 `structured_values.position_code + taxonomy_version` 表示。分类状态、候选岗位、职级、领导范围、技术方向、行业语境、观察到的技能领域、Evidence 和策略版本分别保存，不能再折叠为单一岗位结果。

`capability_profiles.jsonl` 是按统一技能聚合键生成的候选人能力档案；`capability_evidence_links.jsonl` 保存每条工作/项目经历与能力之间的可回溯 Evidence 和 MatchFeature 引用；`capability_verification_profiles.json` 按简历汇总两者。只有直接出现该技能的任务才进入 `supporting_task_feature_ids`，量化结果也必须来自这些直接任务。该派生层采用“有证据加分、无证据不扣分”：声明技能没有经历支持时保留为 `not_observed` 且 `evidence_bonus=0`，不会从抽取结果中删除。
