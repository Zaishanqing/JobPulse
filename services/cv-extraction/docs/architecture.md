# CV 抽取架构

> 文档类型：架构
> 维护状态：active
> 适用范围：`services/cv-extraction/`
> 事实源：模块代码、配置、Schema 与测试
> 责任角色：模块维护者
> 最后复核：2026-07-28

## 当前边界

本版本负责：

- 抽取个人信息、教育、工作、项目、技能、语言、证书、获奖和自我评价。
- 为技能、职责、成果、项目描述、工作/项目技术栈和项目亮点分别保存原文 Evidence。
- 对全部 Evidence 执行连续精确子串校验并填写字符偏移。
- 通过四套对象专属 Schema，为进入 MatchFeature 的个人、教育、工作和项目标量字段建立字段级 Evidence 绑定；PII 不允许进入该绑定。
- 将技能归一化结果作为独立正式产物导出。
- 使用 CV 自有版本化资源 `resources/normalization/2.0/normalization_map.yaml` 作为唯一技能词表。
- 使用 `resources/taxonomy/2.0/skill_taxonomy_snapshot.json` 按标准技能 ID 投影多维分类；
  不从旧 `item_type/category_code` 推断标准分类。
- 生成覆盖全部匹配相关字段的原子 `MatchFeature`；PII 不进入匹配层。
- 输出运行审计、失败案例、审查标记和研究报告。

本版本不判断“候选人声明的能力是否在项目/经历中得到体现”。声明—经历求证将在后续独立派生层实现，不混入本轮抽取 Schema。

## 三层架构

### 1. 抽取层

LLM 只输出原始事实、通用技能类型和 Evidence，不接收完整标准技能词表，也不输出 ID、标准技能 ID、评分或匹配结论。每次请求只附带本份原文中精确命中、且最终确定性门槛本来就要求覆盖的 source-local 技能名称和权威类型，以及 section coverage gate 已要求引用的 source_id/允许集合，减少先遗漏再修复；不暴露 canonical ID，也不改变最终成功条件。Python 在生成 ID 和 Schema 校验之前复刻 JD 的确定性权威字段校正机制。

### 2. 确定性处理与归一化层

Python 负责：

- 生成 `document_id`、`entry_id`、`item_id`。
- 精确对齐全部 Evidence 并填写 `start/end/alignment/occurrence_index`。
- 执行 Schema、语义和业务规则校验。
- 在对象 Schema 阶段检查字段值与字段级 Evidence 的缺失、重复和悬空关系。
- 按 `name + item_type` 精确查表归一化；未命中即为 `unresolved`，不进行近似兜底匹配。
- Python 在模型返回后、生成 ID 与 Schema 校验前，对精确命中标准词表且只有唯一合法类型的 SkillItem 执行权威字段校正；随后再次校验类型契约。
- 在有界预算内执行对象级局部修复或完整重抽取。
- 局部修复除数组对象外，支持对 `personal_info` 进行受控的整体 replace/remove；单例不允许 append。
- 竞赛名次、奖金、荣誉、表彰和获奖证书固定进入 `awards`；职业/语言认证、执业许可和资格凭证固定进入 `certificates`。分类冲突通过受控 remove + append 跨集合移动，并重新执行完整校验。
- 明确的自然语言能力陈述必须进入 `languages`，同一种语言在多处重复声明只建立一个对象；角色标题误作项目、科研课题误作工作时，通过受控 remove + append 移动到正确集合并重新执行完整校验。

### 3. 导出与审计层

抽取结果与归一化结果分别导出，Excel 只是扁平化视图，不是权威数据模型。
标准技能多维分类直接嵌入 `normalized_annotations.json/jsonl` 的技能项；文件输出不再
包含旧 `category_code/subcategory_code` 或独立分类 sidecar。运行时抽取模型与既有
HTTP 契约仍按各自边界维护。

## V2 核心字段

```text
CVExtractionResult
├── document_id                         Python 生成
├── personal_info
├── education[]                         每项有 entry_id + evidence
├── work_experience[]
│   ├── evidence                        经历主体证据
│   ├── tech_stack[]: SkillItem         每项独立证据
│   ├── responsibilities[]: SourcedText 独立证据
│   └── achievements[]: SourcedText     独立证据
├── project_experience[]
│   ├── evidence                        项目主体证据
│   ├── description: SourcedText        独立证据
│   ├── tech_stack[]: SkillItem         每项独立证据
│   └── highlights[]: SourcedText       独立证据
├── skills[]: SkillItem                 每项有 item_id + evidence
├── languages[]                         每项有 entry_id + evidence
├── certificates[]                      每项有 entry_id + evidence
├── awards[]                            每项有 entry_id + evidence
└── self_evaluation[]                   每项有 entry_id + evidence

CVNormalizedResult
├── document_id
├── normalized_skills[]
│   ├── source_item_id
│   ├── source_scope
│   ├── source_name
│   ├── skill_id / canonical_name
│   └── resolution_status
└── unresolved_items[]

CVMatchFeatureResult
└── features[]: MatchFeature
    ├── feature_id / source_object_id / source_scope
    ├── feature_type                  与 JD 匹配层共用类型
    ├── canonical_id / canonical_name
    ├── raw_text / vector_text
    ├── structured_values             年限、学历等级、薪资等
    ├── resolution_status
    └── evidence_refs[]
```

MatchFeature 覆盖技能、软技能、任务、工作与项目经历、岗位、教育、证书、语言、地点、期望薪资、奖项、自我评价、工作状态和到岗时间。岗位特征只包含原文明示的期望岗位和历史工作岗位；没有明确职位的工作经历仍保留，但不生成 `role`。项目角色保留在项目经历特征中，不进入岗位族匹配。姓名、电话、邮箱、性别和出生年份不参与人岗匹配。
